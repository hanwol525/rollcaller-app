"""Ceremony routes: batch render + playback data.

- POST /spaces/{id}/render  -> batch-render every ceremony clip for offline fallback
- GET  /spaces/{id}/ceremony -> ordered roster/clip URLs for player

TTS fallback lives inside the batch renderer:
    ipa = participant.ipa_text or orchestrator.g2p_name(participant.name)
Every participant gets a clip even if they submitted nothing. When the final
IPA source being used is g2p, ipa_source is set to "g2p" even if the user
submitted a separate audio clip.

The render phase (g2p + Kokoro TTS) runs in a thread pool — g2p is an HTTP
call (releases GIL) and Kokoro is CPU inference (torch releases GIL) — so
participants are processed concurrently. DB writes stay serial.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed

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

# Max concurrent render workers. Each holds ~200MB (Kokoro model is shared,
# but torch CPU inference allocates per-call tensors). 4 is a safe ceiling
# for a 4Gi memory limit; tune up if the pod has headroom.
_RENDER_WORKERS = 4


def _get_space_or_404(db: Session, space_id: int) -> Space:
    space = db.get(Space, space_id)
    if space is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Space not found")
    return space


def _render_one(p: Participant) -> tuple[Participant, bytes]:
    """Render a single participant's clip. Returns (participant, wav_bytes).

    Called from a thread pool — both g2p (HTTP) and Kokoro (torch CPU) release
    the GIL, so this parallelizes effectively.
    """
    if p.ipa_text is not None and p.ipa_confirmed:
        ipa_to_render = p.ipa_text
    else:
        # TTS fallback: no confirmed IPA -> use g2p on the name
        ipa_to_render = orchestrator.g2p_name(p.name)
        p.ipa_text = ipa_to_render
        p.ipa_source = "g2p"
        p.ipa_confirmed = True

    p.validate_ipa_invariant()
    wav_bytes = orchestrator.render_clip(p.name, ipa_text=ipa_to_render)
    return p, wav_bytes


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
        ipa_source = "g2p" even if the user previously submitted a separate audio clip.

    The g2p + TTS phase runs concurrently in a thread pool; DB writes are serial.
    """
    _get_space_or_404(db, space_id)
    participants = db.exec(
        select(Participant).where(Participant.space_id == space_id)
    ).all()

    # Phase 1: parallel g2p + Kokoro render (I/O + CPU bound, GIL-releasing)
    results: dict[int, bytes] = {}
    with ThreadPoolExecutor(max_workers=_RENDER_WORKERS) as pool:
        futures = {pool.submit(_render_one, p): p.id for p in participants}
        for future in as_completed(futures):
            p, wav_bytes = future.result()
            results[p.id] = wav_bytes

    # Phase 2: serial DB writes + storage saves
    rendered = 0
    for p in participants:
        wav_bytes = results[p.id]
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