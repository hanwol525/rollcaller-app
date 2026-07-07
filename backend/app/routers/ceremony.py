"""Ceremony routes: batch render + playback data.

- POST /spaces/{id}/render  -> batch-render every ceremony clip for offline fallback
- GET  /spaces/{id}/ceremony -> ordered roster/clip URLs for player

TTS fallback lives inside the batch renderer:
    ipa = participant.ipa_text or orchestrator.g2p_name(participant.name)
Every participant gets a clip even if they submitted nothing. When the final
IPA source being used is g2p, ipa_source is set to "g2p" even if the user
submitted a separate audio clip.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlmodel import Session, select

from app.auth import require_organizer
from app.db import get_session
from app.models import (
    CeremonyData,
    CeremonyItem,
    Organizer,
    Participant,
    Space,
)
from app.pronunciation import orchestrator
from app.storage import storage

router = APIRouter(prefix="/spaces/{space_id}", tags=["ceremony"])


def _get_space_or_404(db: Session, space_id: int) -> Space:
    space = db.get(Space, space_id)
    if space is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Space not found")
    return space


@router.post("/render", status_code=status.HTTP_200_OK)
def render_ceremony(
    space_id: int,
    db: Session = Depends(get_session),
    _org: Organizer = Depends(require_organizer),
):
    """Batch-render every ceremony clip for offline fallback.

    For each participant:
      - ipa = participant.ipa_text or orchestrator.g2p_name(participant.name)
      - render a WAV clip via Kokoro TTS (from IPA when available, else name)
      - store the clip and set clip_key
      - If the final IPA source being used is g2p (TTS fallback), set
        ipa_source = "g2p" even if the user submitted a separate audio clip.
    """
    _get_space_or_404(db, space_id)
    participants = db.exec(
        select(Participant).where(Participant.space_id == space_id)
    ).all()

    rendered = 0
    for p in participants:
        # Determine the IPA to render from.
        if p.ipa_text is not None and p.ipa_confirmed:
            # Use the confirmed IPA (could be recognized, manual, or already g2p)
            ipa_to_render = p.ipa_text
        else:
            # TTS fallback: no confirmed IPA -> use g2p on the name
            ipa_to_render = orchestrator.g2p_name(p.name)
            # CRITICAL: if we're using g2p for the clip, ipa_source MUST be "g2p"
            # even if the user previously submitted a recording.
            p.ipa_text = ipa_to_render
            p.ipa_source = "g2p"
            p.ipa_confirmed = True  # g2p requires ipa_confirmed=True per invariant

        # Enforce the invariant explicitly (table models skip Pydantic validators).
        p.validate_ipa_invariant()

        # Render the clip
        wav_bytes = orchestrator.render_clip(p.name, ipa_text=ipa_to_render)
        # Delete old clip if re-rendering
        if p.clip_key:
            storage.delete(p.clip_key)
        clip_key = storage.save(wav_bytes, ext="wav")
        p.clip_key = clip_key

        db.add(p)
        rendered += 1

    db.commit()
    return {"rendered": rendered}


@router.get("/ceremony", response_model=CeremonyData)
def get_ceremony(
    space_id: int,
    db: Session = Depends(get_session),
    _org: Organizer = Depends(require_organizer),
):
    """Ordered roster/clip URLs for the ceremony player."""
    space = _get_space_or_404(db, space_id)
    participants = db.exec(
        select(Participant)
        .where(Participant.space_id == space_id)
        .order_by(Participant.position)
    ).all()

    items: list[CeremonyItem] = []
    for p in participants:
        clip_url = storage.url(p.clip_key) if p.clip_key else None
        items.append(
            CeremonyItem(
                position=p.position,
                name=p.name,
                clip_url=clip_url,
                ipa_text=p.ipa_text,
                ipa_source=p.ipa_source,
            )
        )

    return CeremonyData(
        space_id=space.id,
        space_name=space.name,
        advanced_seconds=space.advanced_seconds,
        roster=items,
    )