"""Orchestrates the pronunciation pipeline.

Responsibilities:
  - warm_all(): load Kokoro + Allosaurus once at startup (via FastAPI lifespan).
  - recognize_recording(wav_bytes) -> ipa: audio -> IPA via Allosaurus.
  - render_clip(name, ipa_text) -> wav_bytes: IPA -> audio via Kokoro TTS.
  - g2p(name) -> ipa: grapheme -> IPA (delegates to g2p module).

TTS fallback inside the batch renderer looks like:
    ipa = participant.ipa_text or g2p(participant.name)
"""
from __future__ import annotations

from app.pronunciation import g2p, recognize, tts


def warm_all() -> None:
    """Load all heavy models once at startup."""
    g2p.warm()
    recognize.warm()
    tts.warm()


def g2p_name(name: str) -> str:
    """Grapheme-to-phoneme for a name."""
    return g2p.g2p(name)


def recognize_recording(wav_bytes: bytes) -> str:
    """Audio-to-IPA for a participant recording."""
    return recognize.recognize(wav_bytes)


def render_clip(name: str, ipa_text: str | None = None) -> bytes:
    """Render a WAV clip from IPA (preferred) or fall back to grapheme TTS.

    If ipa_text is provided, Kokoro synthesizes from IPA for faithful output.
    Otherwise we synthesize from the raw name string.
    """
    return tts.synthesize(name, ipa=ipa_text)