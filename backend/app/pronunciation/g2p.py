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

DEFAULT_LANGUAGE = "en-us"

# ---------------------------------------------------------------------------
# Per-language backend cache (warmed at startup by the orchestrator, and
# lazily on first use)
# ---------------------------------------------------------------------------
# An EspeakBackend instance is bound to one language, so a single global
# cannot serve several. Keyed by espeak language code.
#
# Membership means warm() has been attempted; a None value means it was
# attempted and failed. That negative entry deliberately diverges from
# tts.py:69 and recognize.py:32, which re-call warm() on every use:
# constructing a backend costs ~2.3 s — ~1.6 s even when it fails — and g2p
# runs once per participant, so retrying would cost minutes per render.
_backends: dict[str, object | None] = {}

# Language codes whose warm failure has been reported, so a batch render
# produces one line rather than one per participant.
_warned_warm_failed: set[str] = set()


def warm(language: str = DEFAULT_LANGUAGE) -> None:
    """Pre-load the phonemizer backend for one language.

    Called at startup via ``orchestrator.warm_all()`` and lazily by
    ``espeak_ipa`` on first use. Gemma is a stateless HTTP call, so there is
    nothing to warm for it.

    Both module containers are mutated in place rather than rebound, so no
    ``global`` is needed — and so a reset must ``.clear()`` them.
    """
    try:
        from phonemizer.backend import EspeakBackend
        _backends[language] = EspeakBackend(
            language=language,
            with_stress=True,
        )
    except Exception as e:
        # phonemizer absent, espeak-ng not loadable, or an unsupported
        # language — the placeholder will be used for this language.
        _backends[language] = None
        if language not in _warned_warm_failed:
            _warned_warm_failed.add(language)
            # The exception type is the whole diagnosis: ModuleNotFoundError
            # means phonemizer is absent, RuntimeError means espeak-ng could
            # not be loaded or the language is not supported.
            log.warning(
                "eSpeak warm failed for language=%r (%s: %s) — g2p will "
                "return placeholder IPA", language, type(e).__name__, e,
                exc_info=True,
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


def espeak_ipa(text: str, language: str = DEFAULT_LANGUAGE) -> str:
    """eSpeak NG grapheme-to-phoneme — the fallback floor.

    Returns **bare** IPA on the working path. Slashes are TTS markup and are
    added at the Kokoro boundary (``tts.py``); ``_fallback`` still wraps, a
    split Unit 4 settles.
    """
    if language not in _backends:
        warm(language)                 # lazy self-heal on first use
    backend = _backends.get(language)
    if backend is not None:
        try:
            result = backend.phonemize([text], strip=True)
            ipa = (result[0] if isinstance(result, list) else str(result)).strip()
            if ipa:
                return ipa
            # A blank name phonemizes to "", which would make g2p() return a
            # falsy string and break its non-empty contract.
            log.warning(
                "eSpeak returned empty IPA for name=%r language=%r",
                text, language,
            )
        except Exception as e:
            log.warning(
                "eSpeak phonemize failed for name=%r language=%r (%s: %s)",
                text, language, type(e).__name__, e,
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
