"""Text-to-speech using Kokoro 82M (CPU).

Loaded lazily. Synthesis feeds IPA via Kokoro's inline pronunciation-override
markup — `[name](/ipa/)` — and falls back to plain text / ASCII-folded text
before failing loud. A broken render raises; it never emits a synthetic tone.
"""
from __future__ import annotations

import io
import logging
import unicodedata

import numpy as np
import soundfile as sf

log = logging.getLogger(__name__)

# Kokoro 82M outputs at a fixed 24000 Hz sample rate.
_KOKORO_SAMPLE_RATE = 24000

# ---------------------------------------------------------------------------
# Warm instance
# ---------------------------------------------------------------------------
_pipeline = None  # type: ignore[var-annotated]


def warm() -> None:
    """Pre-load the Kokoro TTS pipeline. Called once at app startup."""
    global _pipeline
    try:
        from kokoro import KPipeline  # type: ignore[import-not-found]
        _pipeline = KPipeline(lang_code="a")  # American English
    except Exception as e:
        log.warning("Kokoro warm failed: %s", e)
        _pipeline = None


def _ascii_fold(s: str) -> str:
    """Strip diacritics to an ASCII-only form (last-ditch espeak-friendly)."""
    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode("ascii").strip()


def _kokoro_wav(source: str) -> bytes | None:
    """Run one Kokoro pass on `source`; return WAV bytes, or None if no audio."""
    parts = []
    for _gs, _ps, audio in _pipeline(source, voice="af_heart"):
        a = audio.detach().cpu().numpy() if hasattr(audio, "detach") else np.asarray(audio)
        parts.append(a)
    if not parts:
        return None
    audio = np.concatenate(parts)
    buf = io.BytesIO()
    sf.write(buf, audio, _KOKORO_SAMPLE_RATE, format="WAV")
    return buf.getvalue()


def synthesize(text: str, ipa: str | None = None) -> bytes:
    """Synthesize speech for a name, preferring an IPA pronunciation override.

    Attempts, in order:
      1. `[{name}](/{ipa}/)` — faithful IPA via Kokoro's inline markup (if ipa),
      2. `{name}`            — plain text, Kokoro's own G2P,
      3. ASCII-folded `{name}` — diacritics stripped (only if it differs).

    Returns WAV bytes from the first attempt that yields audio. If none do,
    raises RuntimeError — never emits a silent tone.
    """
    if _pipeline is None:
        raise RuntimeError(
            "Kokoro pipeline not loaded — cannot synthesize (check venv / model weights)."
        )

    attempts: list[str] = []
    if ipa:
        attempts.append(f"[{text}](/{ipa}/)")  # faithful IPA — override pronunciation
    attempts.append(text)  # plain name — Kokoro's own G2P
    folded = _ascii_fold(text)
    if folded and folded != text:
        attempts.append(folded)  # last-ditch, diacritics stripped

    last_err: Exception | None = None
    for source in attempts:
        try:
            wav = _kokoro_wav(source)
            if wav:
                return wav
        except Exception as e:
            last_err = e
            log.warning("Kokoro attempt failed (source=%r): %s", source, e)

    # No silent tone. Fail loud so a broken render is visible.
    raise RuntimeError(f"Kokoro produced no audio for name={text!r} ipa={ipa!r}") from last_err