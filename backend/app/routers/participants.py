"""Participant routes: add (single + CSV), list, update, delete, render, ceremony.

All routes under /spaces/ require an organizer session (401 otherwise).
"""
from __future__ import annotations

import csv
import io
import json

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import TypeAdapter
from sqlmodel import Session, select

from app.auth import require_organizer
from app.db import get_session
from app.models import (
    Organizer,
    Participant,
    ParticipantCreate,
    ParticipantRead,
    ParticipantUpdate,
    Space,
)
from app.pronunciation import orchestrator
from app.storage import storage

router = APIRouter(prefix="/spaces/{space_id}/participants", tags=["participants"])


def _to_read(p: Participant) -> ParticipantRead:
    """ParticipantRead, plus the one field that is computed rather than stored.

    ``clip_blocked_reason`` answers "will Prep Clips refuse this participant",
    and it is derived on every read on purpose. A stored column would go stale
    the moment the participant re-records, and it could drift from the
    renderer's own judgement; this calls the same ``orchestrator`` façade
    ``_render_one`` calls, so it cannot. It also needs no migration, which
    matters because ``db.py`` is a bare ``create_all`` with no alembic.

    Two things it deliberately does **not** do:

    - It never runs g2p. A participant with no stored IPA reads as merely
      "not prepared", even though the render might refuse the pronunciation
      g2p would produce for them. Deriving that per row would mean an
      espeak call per participant per page load, now serialised behind
      ``g2p._espeak_lock`` — far too expensive for a read.
    - It does not cover a render that *raised*. That has no row-local
      signature at all, so those rows also read as "not prepared".

    Both make the field under-report rather than over-report, which is the
    right direction: it never claims a participant is blocked when they are
    not.
    """
    return ParticipantRead(
        id=p.id,
        name=p.name,
        email=p.email,
        status=p.status,
        position=p.position,
        ipa_text=p.ipa_text,
        ipa_source=p.ipa_source,
        ipa_confirmed=p.ipa_confirmed,
        invite_token=p.invite_token,
        recording_key=p.recording_key,
        clip_key=p.clip_key,
        clip_blocked_reason=(
            orchestrator.ipa_refusal(p.name, p.ipa_text) if p.ipa_text else None
        ),
    )


def _get_space_or_404(db: Session, space_id: int) -> Space:
    space = db.get(Space, space_id)
    if space is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Space not found")
    return space


def _next_position(db: Session, space_id: int) -> int:
    existing = db.exec(
        select(Participant).where(Participant.space_id == space_id)
    ).all()
    if not existing:
        return 0
    return max(p.position for p in existing) + 1


@router.post("", response_model=list[ParticipantRead], status_code=status.HTTP_201_CREATED)
async def add_participants(
    space_id: int,
    request: Request,
    db: Session = Depends(get_session),
    _org: Organizer = Depends(require_organizer),
):
    """Add one participant (JSON list) or a CSV roster (file upload).

    Returns invite links for all created participants.

    FastAPI can't mix a JSON Body with a File upload in one endpoint, so we
    inspect the content-type and parse accordingly:
      - application/json  -> JSON list of {name, email}
      - multipart/form-data -> csv_file field with a CSV file
    """
    _get_space_or_404(db, space_id)

    created: list[Participant] = []
    content_type = request.headers.get("content-type", "")

    if "application/json" in content_type:
        # JSON body: parse as a list of ParticipantCreate
        raw = await request.body()
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid JSON body",
            )
        if not isinstance(data, list):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Expected a JSON list of participants",
            )
        adapter = TypeAdapter(list[ParticipantCreate])
        try:
            participants = adapter.validate_python(data)
        except Exception:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Each participant must have a 'name' field",
            )
        for pc in participants:
            p = Participant(
                space_id=space_id,
                name=pc.name,
                email=pc.email,
                position=_next_position(db, space_id) + len(created),
            )
            db.add(p)
            created.append(p)

    elif "multipart/form-data" in content_type:
        # File upload: parse the CSV from the csv_file field
        form = await request.form()
        csv_file = form.get("csv_file")
        if csv_file is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Provide a 'csv_file' field with a CSV file",
            )
        raw = await csv_file.read()
        text = raw.decode("utf-8", errors="replace")
        reader = csv.DictReader(io.StringIO(text))
        # Accept headers: name,email (email optional)
        if reader.fieldnames is None or "name" not in reader.fieldnames:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="CSV must have a 'name' column (email optional)",
            )
        for row in reader:
            name = (row.get("name") or "").strip()
            if not name:
                continue
            email = (row.get("email") or "").strip() or None
            p = Participant(
                space_id=space_id,
                name=name,
                email=email,
                position=_next_position(db, space_id) + len(created),
            )
            db.add(p)
            created.append(p)

    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Provide a JSON list of participants or a CSV file upload",
        )

    db.commit()
    for p in created:
        db.refresh(p)
    return [_to_read(p) for p in created]


@router.get("", response_model=list[ParticipantRead])
def list_participants(
    space_id: int,
    db: Session = Depends(get_session),
    _org: Organizer = Depends(require_organizer),
):
    _get_space_or_404(db, space_id)
    rows = db.exec(
        select(Participant)
        .where(Participant.space_id == space_id)
        .order_by(Participant.position)
    ).all()
    return [_to_read(p) for p in rows]


@router.put("/{pid}", response_model=ParticipantRead)
def update_participant(
    space_id: int,
    pid: int,
    body: ParticipantUpdate,
    db: Session = Depends(get_session),
    _org: Organizer = Depends(require_organizer),
):
    _get_space_or_404(db, space_id)
    p = db.get(Participant, pid)
    if p is None or p.space_id != space_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Participant not found")
    if body.name is not None:
        p.name = body.name
    if body.email is not None:
        p.email = body.email
    if body.position is not None:
        p.position = body.position
    db.add(p)
    db.commit()
    db.refresh(p)
    # Through the builder because this route can change the **name**, and the
    # guard's letter-name rule counts the initials in it -- so a rename can
    # change whether this participant is blocked.
    return _to_read(p)


@router.delete("/{pid}", status_code=status.HTTP_204_NO_CONTENT)
def delete_participant(
    space_id: int,
    pid: int,
    db: Session = Depends(get_session),
    _org: Organizer = Depends(require_organizer),
):
    _get_space_or_404(db, space_id)
    p = db.get(Participant, pid)
    if p is None or p.space_id != space_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Participant not found")
    # Clean up stored blobs
    if p.recording_key:
        storage.delete(p.recording_key)
    if p.clip_key:
        storage.delete(p.clip_key)
    db.delete(p)
    db.commit()
    return None