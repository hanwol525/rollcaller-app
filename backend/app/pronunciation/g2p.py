"""Grapheme-to-phoneme conversion for the g2p fallback path.

The primary engine is Gemma 4 (called over an OpenAI-compatible endpoint);
eSpeak NG via the `phonemizer` library is the fallback floor. Both run only at
prep-clips time, never in the live ceremony path.

The public entry point ``g2p(text)`` preserves the signature all existing
callers expect. The result is still tagged ``IpaSource.g2p`` upstream — the
engine behind it is an implementation detail.
"""
from __future__ import annotations

import logging

from app.pronunciation.gemma import gemma_ipa

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Warm instance (loaded once at startup by the orchestrator)
# ---------------------------------------------------------------------------
_backend = None  # type: ignore[var-annotated]

# Set once the warm failure has been reported. A lazy self-heal calls warm()
# on every use, and g2p runs once per participant in a batch render, so
# reporting per call would be one copy of the same line per name.
_warned_warm_failed = False


def warm() -> None:
    """Pre-load the phonemizer backend. Called once at app startup.

    Gemma is a stateless HTTP call, so there is nothing to warm for it.
    """
    global _backend, _warned_warm_failed
    try:
        from phonemizer import backend as _b
        _backend = _b.Backend(
            language="en-us",
            backend="espeak",
            with_stress=True,
        )
    except Exception as e:
        # espeak-ng / phonemizer not installed — fallback will be used.
        _backend = None
        if not _warned_warm_failed:
            _warned_warm_failed = True
            # The exception *type* is the whole diagnosis, and all of these
            # currently look identical downstream: ModuleNotFoundError means
            # phonemizer is absent, AttributeError means the symbol above is
            # wrong, TypeError means a bad kwarg, RuntimeError means espeak-ng
            # itself could not be found.
            log.warning(
                "eSpeak warm failed (%s: %s) — g2p will return placeholder IPA",
                type(e).__name__, e, exc_info=True,
            )


def g2p(text: str) -> str:
    """Convert a grapheme string (a name) to an IPA string.

    Tries Gemma 4 first; on any failure (missing config, network error,
    timeout, empty/implausible output) ``gemma_ipa`` returns ``None`` and we
    fall through to the eSpeak floor. The signature is unchanged from the
    original rule-based g2p, so all callers (orchestrator, ceremony renderer)
    keep working with no edits.
    """
    return gemma_ipa(text) or espeak_ipa(text)


def espeak_ipa(text: str) -> str:
    """eSpeak NG grapheme-to-phoneme — the fallback floor.

    Produces IPA in the same bracketing/spacing convention the pipeline has
    always used, so ``gemma_ipa`` is a literal drop-in for this function.
    """
    if _backend is not None:
        try:
            result = _backend.phonemize([text], strip=True)
            if isinstance(result, list):
                return result[0].strip()
            return str(result).strip()
        except Exception as e:
            log.warning(
                "eSpeak phonemize failed for name=%r (%s: %s)",
                text, type(e).__name__, e,
            )
    # Fallback: very rough ASCII approximation so the pipeline still works
    # in environments without espeak-ng (e.g. CI / test).
    return _fallback(text)


def _fallback(text: str) -> str:
    """Crude placeholder IPA. NOT linguistically accurate — for dev only."""
    # Just return the text in slashes so it's distinguishable from real IPA.
    cleaned = text.strip().lower().replace(" ", "_")
    placeholder = f"/{cleaned}/"
    # Per call, deliberately not deduped: this is the placeholder reaching a
    # real ceremony, so which names it happened to is the whole point.
    log.warning(
        "placeholder IPA used for name=%r -> %r; this is spelling, not a "
        "pronunciation", text, placeholder,
    )
    return placeholder
