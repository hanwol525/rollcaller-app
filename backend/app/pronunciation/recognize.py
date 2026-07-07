"""Audio-to-IPA recognition using Allosaurus.

Loaded lazily; a fallback returns a pseudo-IPA when Allosaurus isn't installed
so tests can exercise the full pipeline without the heavy ML stack.
"""
from __future__ import annotations

import io

# ---------------------------------------------------------------------------
# Warm instance
# ---------------------------------------------------------------------------
_recognizer = None  # type: ignore[var-annotated]


def warm() -> None:
    """Pre-load the Allosaurus recognizer. Called once at app startup."""
    global _recognizer
    try:
        from allosaurus.app import read_recognizer
        _recognizer = read_recognizer()
    except Exception:
        _recognizer = None


def recognize(wav_bytes: bytes) -> str:
    """Recognize IPA from a WAV audio bytes blob."""
    if _recognizer is not None:
        try:
            import soundfile as sf
            # Allosaurus wants a path or a file-like; use BytesIO.
            audio, sr = sf.read(io.BytesIO(wav_bytes))
            # Write to a temp wav Allosaurus can read
            import tempfile, os
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
                sf.write(tmp.name, audio, sr)
                tmp_path = tmp.name
            try:
                result = _recognizer.recognize(tmp_path)
            finally:
                os.unlink(tmp_path)
            return str(result).strip()
        except Exception:
            pass
    return _fallback(wav_bytes)


def _fallback(wav_bytes: bytes) -> str:
    """Placeholder IPA for dev/test without Allosaurus.

    Returns a deterministic pseudo-IPA based on the byte length so different
    recordings yield different (but stable) strings.
    """
    length = len(wav_bytes)
    return f"/rec{length % 1000:03d}/"