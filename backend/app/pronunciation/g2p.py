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
import threading

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

# The only lock in the codebase, and it guards a C library rather than Python
# state. espeak-ng keeps its phoneme buffers in library globals -- phonemizer
# says so itself, and sidesteps it by copying the shared library per wrapper
# instance (phonemizer/backend/espeak/api.py, EspeakAPI.__init__) -- while
# _backends deliberately shares ONE instance per language. Under
# ceremony.py's 4-worker pool that means concurrent phonemize() calls return
# each other's phonemes, which the renderer then stored and voiced as that
# person's pronunciation with no failure reported. It is a race, so the rate
# moves with timing and roster size -- measured anywhere from 52% to 87% of
# concurrent calls returning the wrong participant's IPA. Never zero.
#
# Everywhere else, thread safety here comes from worker purity plus phase
# separation (ceremony.py:12-15). That is still right for the ORM; it simply
# never covered a C library with global buffers under a pure Python function.
#
# Reentrant because espeak_ipa holds it across its lazy warm() call and
# warm() takes it too. Construction needs the lock in its own right, not
# merely to avoid duplicated work: EspeakAPI.__init__ loads its own copy of
# the library and calls espeak_Initialize, so building a backend while
# another thread is inside phonemize() is itself unsafe.
_espeak_lock = threading.RLock()


def warm(language: str = DEFAULT_LANGUAGE) -> None:
    """Pre-load the phonemizer backend for one language.

    Called at startup via ``orchestrator.warm_all()`` and lazily by
    ``espeak_ipa`` on first use. Gemma is a stateless HTTP call, so there is
    nothing to warm for it.

    Both module containers are mutated in place rather than rebound, so no
    ``global`` is needed — and so a reset must ``.clear()`` them.

    Takes ``_espeak_lock``: ``EspeakAPI.__init__`` calls ``espeak_Initialize``
    on its library copy, so constructing a backend while another thread sits
    inside ``phonemize()`` is unsafe on its own — and two workers racing a
    cold cache would each pay ~2.3 s to build one that gets discarded.
    """
    with _espeak_lock:
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

    Returns **bare** IPA. Slashes are TTS markup, added only at the Kokoro
    boundary (``tts.py``). When eSpeak cannot help, ``_fallback`` returns ""
    rather than inventing a pronunciation.
    """
    # Serialized: the shared backend's phonemes come out of espeak-ng's
    # library globals, so two workers calling at once get each other's. See
    # _espeak_lock. The lock does NOT extend to gemma_ipa -- g2p() calls that
    # first, and it is an HTTP request that must stay parallel.
    with _espeak_lock:
        if language not in _backends:
            warm(language)                 # lazy self-heal on first use
        backend = _backends.get(language)
        if backend is not None:
            try:
                result = backend.phonemize([text], strip=True)
                ipa = (result[0] if isinstance(result, list) else str(result)).strip()
                if ipa:
                    return ipa
                # A blank name phonemizes to "". Worth distinguishing from a
                # backend failure even though both end at _fallback.
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
    """No IPA available. Returns "" instead of inventing a pronunciation.

    The empty string is deliberate, not a placeholder. ``tts.py`` guards its
    pronunciation override with ``if ipa:``, so an empty value makes Kokoro
    skip the override and voice the name with its own G2P — an honest English
    attempt, where the old ``/name_with_underscores/`` got the spelling read
    back as phonemes.

    It is "" and not None because ``ceremony.py`` writes this straight to
    ``ipa_text`` alongside ``ipa_source="g2p"``, and None there trips
    ``validate_ipa_invariant``'s both-or-neither rule, failing the whole batch.
    Refusing to render the name at all needs ``ceremony.py``, which this brief
    fences off.
    """
    # Per call, deliberately not deduped: this is a real ceremony getting no
    # pronunciation, so which names it happened to is the whole point.
    log.warning(
        "no IPA for name=%r — Kokoro will voice the name directly, which may "
        "mispronounce it", text,
    )
    return ""
