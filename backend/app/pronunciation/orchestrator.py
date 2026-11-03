"""Orchestrates the pronunciation pipeline.

Responsibilities:
  - warm_all(): load the four stages once at startup (via the FastAPI
    lifespan) — eSpeak g2p, the wav2vec2 recognizer, Kokoro TTS, ffmpeg.
  - recognize_recording(wav_bytes) -> ipa: audio -> IPA via wav2vec2.
  - render_clip(name, ipa_text) -> wav_bytes: IPA -> audio via Kokoro TTS.
  - g2p_name(name) -> ipa: grapheme -> IPA (delegates to the g2p module).

TTS fallback inside the batch renderer looks like:
    ipa = participant.ipa_text or g2p(participant.name)
"""
from __future__ import annotations

from app.pronunciation import g2p, recognize, transcode, tts


def warm_all() -> None:
    """Load all heavy models once at startup."""
    g2p.warm()
    recognize.warm()
    tts.warm()
    transcode.warm()


def g2p_name(name: str) -> str:
    """Grapheme-to-phoneme for a name."""
    return g2p.g2p(name)


def normalize_recording(raw: bytes) -> bytes:
    """Normalize browser audio (webm/opus, mp4/aac, ...) to canonical WAV."""
    return transcode.to_wav(raw)


def recognize_recording(wav_bytes: bytes) -> str:
    """Audio-to-IPA for a participant recording."""
    return recognize.recognize(wav_bytes)


def render_clip(name: str, ipa_text: str | None = None) -> bytes:
    """Render a WAV clip from IPA (preferred) or fall back to grapheme TTS.

    If ipa_text is provided, Kokoro synthesizes from IPA for faithful output.
    Otherwise we synthesize from the raw name string.
    """
    return tts.synthesize(name, ipa=ipa_text)