"""Text-to-speech using Kokoro 82M (CPU).

Loaded lazily; a fallback generates a deterministic sine-wave WAV when Kokoro
isn't installed so tests can exercise the full pipeline without the ML stack.
"""
from __future__ import annotations

import io
import math
import struct
import wave

# ---------------------------------------------------------------------------
# Warm instance
# ---------------------------------------------------------------------------
_tts = None  # type: ignore[var-annotated]


def warm() -> None:
    """Pre-load the Kokoro TTS model. Called once at app startup."""
    global _tts
    try:
        from kokoro import Kokoro  # type: ignore[import-not-found]
        _tts = Kokoro()
    except Exception:
        _tts = None


def synthesize(text: str, ipa: str | None = None) -> bytes:
    """Synthesize speech from text or IPA. Returns WAV bytes.

    If `ipa` is provided we attempt to feed it to Kokoro's phoneme input;
    otherwise we fall back to grapheme input via `text`.
    """
    if _tts is not None:
        try:
            # Kokoro's API: generate from text or phonemes.
            # We prefer IPA when available for faithful pronunciation.
            if ipa:
                out = _tts.create(ipa, lang="en-us")
            else:
                out = _tts.create(text, lang="en-us")
            # Kokoro returns a numpy array + sample rate; convert to WAV bytes.
            import numpy as np
            import soundfile as sf
            buf = io.BytesIO()
            audio = out.audio if hasattr(out, "audio") else out[0]
            sr = out.sample_rate if hasattr(out, "sample_rate") else out[1]
            sf.write(buf, audio, sr, format="WAV")
            return buf.getvalue()
        except Exception:
            pass
    return _fallback(text, ipa)


def _fallback(text: str, ipa: str | None = None) -> bytes:
    """Generate a short deterministic sine-wave WAV for dev/test.

    The tone frequency is derived from the input length so different names
    produce different (but stable) audio.
    """
    freq = 220.0 + (len(text) % 20) * 10
    sample_rate = 16000
    duration = 0.5
    n_samples = int(sample_rate * duration)

    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)  # 16-bit
        w.setframerate(sample_rate)
        for i in range(n_samples):
            sample = int(16000 * math.sin(2 * math.pi * freq * (i / sample_rate)))
            w.writeframes(struct.pack("<h", sample))
    return buf.getvalue()