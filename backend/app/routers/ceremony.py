"""Ceremony routes: batch render + playback data.

- POST /spaces/{id}/render  -> batch-render every ceremony clip for offline fallback
- GET  /spaces/{id}/ceremony -> ordered roster/clip URLs for player

TTS fallback lives inside the batch renderer: a participant with no stored
pronunciation gets one from g2p. A stored one always wins — recognition and
confirmation both set ipa_confirmed, so a recording is never regenerated.

The render phase (g2p + Kokoro TTS) runs in a thread pool — g2p is an HTTP
call (releases GIL) and Kokoro is CPU inference (torch releases GIL) — so
participants are processed concurrently. Workers are **pure**: every DB write
and the single commit happen on the request thread, and superseded clips are
deleted only *after* that commit. So a failure can leak a blob, but can never
leave a row pointing at a deleted file.

A participant whose pronunciation the TTS guard **refuses** keeps no clip and
is named in the response — rather than silently getting a clip voiced from the
spelling of their name. If they held a clip from an earlier run, it is cleared
and the blob is deleted, in the same single post-commit loop as a superseded
one, so the durable state cannot contradict the report.

A render that **raises** is treated differently and deliberately so: it is
named in the response, but any clip it already had is left alone. A refusal is
a judgement that will repeat; a raise is an unknown and usually transient, and
silence at a ceremony is worse than a clip that worked yesterday.
"""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import NamedTuple

from fastapi import APIRouter, Depends, HTTPException, status
from sqlmodel import Session, select

from app.auth import require_organizer
from app.db import get_session
from app.models import (
    CeremonyData,
    CeremonyItem,
    IPASource,
    Organizer,
    Participant,
    RenderFailure,
    RenderReport,
    Space,
)
from app.pronunciation import orchestrator
from app.storage import storage

log = logging.getLogger(__name__)

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


class _Outcome(NamedTuple):
    """What one worker produced. `reason` set means no clip for this name."""
    participant_id: int
    wav_bytes: bytes | None
    ipa_used: str | None
    becomes_g2p: bool
    reason: str | None


def _render_one(p: Participant) -> _Outcome:
    """Render one participant's clip. **Pure** — no ORM writes, no DB access.

    Runs in a worker thread, so it must not touch the Session-owned instance:
    writing here was AUDIT.md §4.7's first defect, and once partial results
    commit it would stamp g2p provenance onto participants whose render
    failed. Phase 2 applies every change on the request thread instead.

    A stored pronunciation always wins. The confirmation flag is deliberately
    not consulted: by invariant Rule 3 an unconfirmed entry is always
    "manual", a state no route can produce, so requiring confirmation here
    would only ever discard a pronunciation someone put there on purpose.
    """
    if p.ipa_text is not None:
        ipa, becomes_g2p = p.ipa_text, False
    else:
        ipa, becomes_g2p = orchestrator.g2p_name(p.name), True

    # An empty IPA means the g2p floor had nothing to offer; Kokoro would fall
    # back to guessing from the spelling. Both count as refusals here.
    refusal = (
        orchestrator.ipa_refusal(p.name, ipa) if ipa
        else "no pronunciation could be derived from the name"
    )
    if refusal:
        return _Outcome(p.id, None, ipa, becomes_g2p, refusal)

    wav_bytes = orchestrator.render_clip(p.name, ipa_text=ipa)
    return _Outcome(p.id, wav_bytes, ipa, becomes_g2p, None)


@router.post("/render", response_model=RenderReport, status_code=status.HTTP_200_OK)
def render_ceremony(
    space_id: int,
    db: Session = Depends(get_session),
    _org: Organizer = Depends(require_organizer),
):
    """Batch-render every ceremony clip for offline fallback.

    Partial success is a success: a participant whose render raises, or whose
    pronunciation the TTS guard refuses, gets no new clip and is named in the
    response. A refusal also withdraws any clip they held; a raise keeps it
    (see the module docstring). Everyone else still gets theirs. Previously
    one bad name 500'd the batch and persisted nothing.
    """
    _get_space_or_404(db, space_id)
    participants = db.exec(
        select(Participant).where(Participant.space_id == space_id)
    ).all()
    by_id = {p.id: p for p in participants}

    # Phase 1: parallel g2p + Kokoro (I/O + CPU bound, GIL-releasing). Each
    # future is guarded, so one bad name cannot discard the roster's work.
    outcomes: dict[int, _Outcome] = {}
    failures: list[RenderFailure] = []
    # Phase 2 sees only `outcomes`, where a refusal and a raise are both just
    # an absent entry -- but they must not be treated the same (see the module
    # docstring), so the distinction is carried forward explicitly.
    refused: set[int] = set()
    with ThreadPoolExecutor(max_workers=_RENDER_WORKERS) as pool:
        futures = {pool.submit(_render_one, p): p.id for p in participants}
        for future in as_completed(futures):
            name = by_id[futures[future]].name
            try:
                outcome = future.result()
            except Exception as e:
                log.warning(
                    "render failed for name=%r (%s: %s)", name, type(e).__name__, e,
                )
                failures.append(
                    RenderFailure(name=name, reason=f"{type(e).__name__}: {e}")
                )
                continue
            if outcome.reason:
                log.warning("render refused for name=%r: %s", name, outcome.reason)
                failures.append(RenderFailure(name=name, reason=outcome.reason))
                refused.add(outcome.participant_id)
                continue
            outcomes[outcome.participant_id] = outcome

    # Phase 2: DB writes and storage saves, serial, on the request thread.
    # One list for every blob this request orphans -- superseded by a fresh
    # render, or withdrawn from a refused participant. Both are deleted in the
    # single loop after the commit; neither is deleted here.
    stale: list[str] = []
    rendered = 0
    for p in participants:
        outcome = outcomes.get(p.id)
        if outcome is None:
            if p.id in refused and p.clip_key:
                # The clip a refused participant holds came from confirm_ipa,
                # where synthesize *degrades* rather than refuses -- so it is
                # a reading of their spelling, the one thing a graduation must
                # not broadcast. The report says they have no pronunciation;
                # GET /ceremony has to agree.
                #
                # Provenance is left untouched on purpose: once the
                # pronunciation is fixed, one more Prep Clips is a full
                # recovery with no participant involvement.
                stale.append(p.clip_key)
                p.clip_key = None
                db.add(p)
            continue        # a raise keeps its clip -- see the module docstring
        if outcome.becomes_g2p:
            p.ipa_text = outcome.ipa_used
            p.ipa_source = IPASource.g2p.value
            p.ipa_confirmed = True
        p.validate_ipa_invariant()
        if p.clip_key:
            stale.append(p.clip_key)        # remembered, not deleted yet
        p.clip_key = storage.save(outcome.wav_bytes, ext="wav")
        db.add(p)
        rendered += 1

    db.commit()

    # Only now is deleting safe. Before the commit, a dropped connection would
    # roll back every new clip_key while the old blobs were already gone --
    # rows pointing at deleted files, which is AUDIT.md §4.3's catastrophe by
    # another route. After it, the worst case is a leaked blob.
    # Stronger for a withdrawn clip than for a superseded one: the row stopped
    # referencing the key *before* the commit, so even a failed delete leaves
    # no row pointing at a missing file. storage.delete is a no-op on a key
    # that is already gone, in both backends.
    for key in stale:
        try:
            storage.delete(key)
        except Exception as e:
            log.warning(
                "orphaned clip %r left in storage (%s: %s)", key, type(e).__name__, e,
            )

    return RenderReport(rendered=rendered, failures=failures)


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
                # Computed, same as on the roster: lets the player tell a
                # roster nobody has prepped from one where every name was
                # refused -- a state this brief's render changes made
                # reachable. See participants._to_read for the caveats.
                clip_blocked_reason=(
                    orchestrator.ipa_refusal(p.name, p.ipa_text)
                    if p.ipa_text is not None else None
                ),
            )
        )

    return CeremonyData(
        space_id=space.id,
        space_name=space.name,
        advanced_seconds=space.advanced_seconds,
        roster=items,
    )