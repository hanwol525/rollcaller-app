"""Invite routes: participant self-service via shareable invite link.

Authorization is by token alone — no login. Bad tokens return 404 (capability
token pattern: possession determines permission).

Routes:
  GET  /invite/{token}            -> ParticipantSelf (participant's own view)
  POST /invite/{token}/recording  -> upload audio, return first-pass IPA
  POST /invite/{token}/ipa/preview -> render IPA to audio (NO writes, return WAV)
  POST /invite/{token}/ipa/confirm -> lock IPA + render individual clip

Rules:
  - Previews do NOT store/render anything to storage; only confirm writes.
  - ipa_text and ipa_source move together (both None or both filled).
  - If ipa_source is g2p or recognized, ipa_confirmed must be True.
  - Only a human edit is "manual".
  - On confirm: human edits to IPA set ipa_source="manual"; confirming an
    unchanged IPA keeps the prior source. ipa_confirmed=True, status=confirmed,
    clip_key populated with the stored clip.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, File, HTTPException, Response, UploadFile, status
from sqlmodel import Session, select

from app.db import get_session
from app.models import (
    IPAConfirmRequest,
    IPAPreviewRequest,
    Participant,
    ParticipantSelf,
    Space,
)
from app.pronunciation import orchestrator
from app.storage import storage

router = APIRouter(prefix="/invite", tags=["invite"])


def _get_participant_or_404(db: Session, token: str) -> Participant:
    """Token possession determines permission. Bad token = 404."""
    p = db.exec(
        select(Participant).where(Participant.invite_token == token)
    ).first()
    if p is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    return p


def _build_participant_self(db: Session, p: Participant) -> ParticipantSelf:
    """Construct ParticipantSelf, looking up the space name for participant screens."""
    space = db.get(Space, p.space_id)
    return ParticipantSelf(
        id=p.id,
        name=p.name,
        space_name=space.name if space else "",
        status=p.status,
        ipa_text=p.ipa_text,
        ipa_confirmed=p.ipa_confirmed,
    )


@router.get("/{token}", response_model=ParticipantSelf)
def get_self(token: str, db: Session = Depends(get_session)):
    p = _get_participant_or_404(db, token)
    return _build_participant_self(db, p)


@router.post("/{token}/recording", response_model=ParticipantSelf)
async def upload_recording(
    token: str,
    db: Session = Depends(get_session),
    file: UploadFile = File(...),
):
    """Upload a WAV recording. Stores the blob, runs Allosaurus recognition,
    sets ipa_text/ipa_source to the recognized IPA, and switches status to
    'recorded'.

    Per the model invariant, ipa_source='recognized' requires ipa_confirmed=True.
    The participant can then preview/edit and confirm via the other endpoints.
    """
    p = _get_participant_or_404(db, token)

    raw = await file.read()
    if not raw:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Empty audio file",
        )

    # Normalize browser audio (webm/opus, mp4/aac, ...) → canonical 16 kHz
    # mono WAV before anything reads it. One transcode fixes both the
    # recognition input and the stored blob (which was mislabeled .wav).
    # RuntimeError (ffmpeg absent + non-WAV) → 500 (mis-provisioned env).
    try:
        wav = orchestrator.normalize_recording(raw)
    except RuntimeError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(exc),
        )

    # Run audio-to-IPA recognition (Allosaurus) BEFORE saving the blob,
    # so a recognition failure doesn't leave an orphaned file on disk.
    ipa = orchestrator.recognize_recording(wav)
    if not ipa:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Could not recognize IPA from audio",
        )

    # Store the recording blob (now honestly WAV, not raw browser bytes)
    if p.recording_key:
        storage.delete(p.recording_key)
    recording_key = storage.save(wav, ext="wav")
    p.recording_key = recording_key

    # Set ipa_text + ipa_source together (they move together).
    # recognized requires ipa_confirmed=True per the model invariant.
    p.ipa_text = ipa
    p.ipa_source = "recognized"
    p.ipa_confirmed = True
    p.status = "recorded"

    # Enforce the invariant explicitly (table models skip Pydantic validators).
    p.validate_ipa_invariant()

    db.add(p)
    db.commit()
    db.refresh(p)

    return _build_participant_self(db, p)


@router.post("/{token}/ipa/preview")
def preview_ipa(
    token: str,
    body: IPAPreviewRequest,
    db: Session = Depends(get_session),
):
    """Render an IPA string to audio and return a WAV file.

    CRITICAL: This does NOT store or persist anything. No writes to storage
    or the database. Only the confirmed version makes it to storage.
    """
    p = _get_participant_or_404(db, token)

    # Render to WAV bytes in memory — do NOT call storage.save()
    wav_bytes = orchestrator.render_clip(p.name, ipa_text=body.ipa)

    return Response(
        content=wav_bytes,
        media_type="audio/wav",
        headers={"Content-Disposition": "inline; filename=preview.wav"},
    )


@router.post("/{token}/ipa/confirm", response_model=ParticipantSelf)
def confirm_ipa(
    token: str,
    body: IPAConfirmRequest,
    db: Session = Depends(get_session),
):
    """Lock the IPA and render the individual clip.

    Sets ipa_confirmed=True, status=confirmed, clip_key=populated.
    Any human edits to the IPA set ipa_source="manual" except if the change
    is unconfirmed (which leaves the prior source).
    """
    p = _get_participant_or_404(db, token)

    prior_ipa = p.ipa_text
    prior_source = p.ipa_source

    # Determine if this is a human edit (IPA changed from what was there)
    is_human_edit = body.is_edit and (prior_ipa is None or body.ipa != prior_ipa)

    # Set the IPA text
    p.ipa_text = body.ipa

    # Determine ipa_source:
    # - If human edit and confirming: ipa_source = "manual"
    # - If no edit (confirming existing recognized/g2p IPA): keep prior source
    # - If no prior source (first confirmation of a manually typed IPA): "manual"
    if is_human_edit:
        p.ipa_source = "manual"
    elif prior_source is not None:
        # Keep the prior source (recognized, g2p, or manual)
        p.ipa_source = prior_source
    else:
        # No prior source and confirming a manually entered IPA
        p.ipa_source = "manual"

    # Confirm: lock it
    p.ipa_confirmed = True
    p.status = "confirmed"

    # Enforce the invariant explicitly (table models skip Pydantic validators).
    p.validate_ipa_invariant()

    # Render and store the clip (only confirmed version goes to storage)
    wav_bytes = orchestrator.render_clip(p.name, ipa_text=p.ipa_text)
    if p.clip_key:
        storage.delete(p.clip_key)
    p.clip_key = storage.save(wav_bytes, ext="wav")

    db.add(p)
    db.commit()
    db.refresh(p)

    return _build_participant_self(db, p)
