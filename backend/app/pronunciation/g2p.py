"""Grapheme-to-phoneme conversion for the g2p fallback path.

The primary engine is Gemma 4 (called over an OpenAI-compatible endpoint);
eSpeak NG via the `phonemizer` library is the fallback floor. Both run only at
prep-clips time, never in the live ceremony path.

The public entry point ``g2p(text)`` preserves the signature all existing
callers expect. The result is still tagged ``IpaSource.g2p`` upstream — the
engine behind it is an implementation detail.
"""
from __future__ import annotations

from app.pronunciation.gemma import gemma_ipa

# ---------------------------------------------------------------------------
# Warm instance (loaded once at startup by the orchestrator)
# ---------------------------------------------------------------------------
_backend = None  # type: ignore[var-annotated]


def warm() -> None:
    """Pre-load the phonemizer backend. Called once at app startup.

    Gemma is a stateless HTTP call, so there is nothing to warm for it.
    """
    global _backend
    try:
        from phonemizer import backend as _b
        _backend = _b.Backend(
            language="en-us",
            backend="espeak",
            with_stress=True,
        )
    except Exception:
        # espeak-ng / phonemizer not installed — fallback will be used.
        _backend = None


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
        except Exception:
            pass
    # Fallback: very rough ASCII approximation so the pipeline still works
    # in environments without espeak-ng (e.g. CI / test).
    return _fallback(text)


def _fallback(text: str) -> str:
    """Crude placeholder IPA. NOT linguistically accurate — for dev only."""
    # Just return the text in slashes so it's distinguishable from real IPA.
    cleaned = text.strip().lower().replace(" ", "_")
    return f"/{cleaned}/"
