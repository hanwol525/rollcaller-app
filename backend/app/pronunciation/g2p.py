"""Grapheme-to-phoneme conversion using Espeak NG via the `phonemizer` library.

Loaded lazily so the heavy dependency isn't required for tests. A lightweight
fallback returns a simple pseudo-IPA when phonemizer/espeak aren't available.
"""
from __future__ import annotations

# ---------------------------------------------------------------------------
# Warm instance (loaded once at startup by the orchestrator)
# ---------------------------------------------------------------------------
_backend = None  # type: ignore[var-annotated]


def warm() -> None:
    """Pre-load the phonemizer backend. Called once at app startup."""
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
    """Convert a grapheme string (a name) to an IPA string."""
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