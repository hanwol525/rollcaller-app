"""Space routes: create, list, get, update, delete.

All routes under /spaces/ require an organizer session (401 otherwise).
Delete cascades to participants.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlmodel import Session, select

from app.auth import require_organizer
from app.db import get_session
from app.models import (
    Organizer,
    Participant,
    ParticipantRead,
    Space,
    SpaceCreate,
    SpaceRead,
    SpaceUpdate,
)

router = APIRouter(prefix="/spaces", tags=["spaces"])


@router.post("", response_model=SpaceRead, status_code=status.HTTP_201_CREATED)
def create_space(
    body: SpaceCreate,
    db: Session = Depends(get_session),
    _org: Organizer = Depends(require_organizer),
):
    space = Space(name=body.name, advanced_seconds=body.advanced_seconds)
    db.add(space)
    db.commit()
    db.refresh(space)
    return space


@router.get("", response_model=list[SpaceRead])
def list_spaces(
    db: Session = Depends(get_session),
    _org: Organizer = Depends(require_organizer),
):
    return db.exec(select(Space).order_by(Space.created_at.desc())).all()


@router.get("/{space_id}", response_model=SpaceRead)
def get_space(
    space_id: int,
    db: Session = Depends(get_session),
    _org: Organizer = Depends(require_organizer),
):
    space = db.get(Space, space_id)
    if space is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Space not found")
    return space


@router.put("/{space_id}", response_model=SpaceRead)
def update_space(
    space_id: int,
    body: SpaceUpdate,
    db: Session = Depends(get_session),
    _org: Organizer = Depends(require_organizer),
):
    space = db.get(Space, space_id)
    if space is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Space not found")
    if body.name is not None:
        space.name = body.name
    if body.advanced_seconds is not None:
        space.advanced_seconds = body.advanced_seconds
    db.add(space)
    db.commit()
    db.refresh(space)
    return space


@router.delete("/{space_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_space(
    space_id: int,
    db: Session = Depends(get_session),
    _org: Organizer = Depends(require_organizer),
):
    space = db.get(Space, space_id)
    if space is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Space not found")
    # Manual cascade: delete all participants in this space.
    participants = db.exec(
        select(Participant).where(Participant.space_id == space_id)
    ).all()
    for p in participants:
        db.delete(p)
    db.delete(space)
    db.commit()
    return None