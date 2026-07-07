"""Normalize inbound browser audio to canonical 16 kHz mono PCM WAV.

Browser ``MediaRecorder`` does NOT produce WAV. It produces:
  - ``audio/webm`` (Opus) — Chrome, Firefox, Edge
  - ``audio/mp4`` (AAC) — Safari

``soundfile`` / Allosaurus cannot decode either, and the stored blob was
mislabeled as ``.wav``. This module transcodes arbitrary browser audio to
canonical 16 kHz mono WAV via the system ``ffmpeg`` binary **the moment bytes
arrive** — before recognition and before storage — so one transcode fixes both
problems at once.

Graceful passthrough (matches the rest of the pronunciation pipeline):
  - ffmpeg present  -> transcode.
  - ffmpeg absent AND bytes already WAV -> pass through untouched.
  - ffmpeg absent AND bytes NOT WAV     -> raise a clear RuntimeError
    (mis-provisioned environment; fail loud with a diagnostic, never a cryptic
    ``soundfile`` stack trace). The route maps this to HTTP 500.
"""
from __future__ import annotations

import shutil
import subprocess

# ---------------------------------------------------------------------------
# Warm: cache the ffmpeg path lookup once at startup so we don't probe PATH
# on every request.
# ---------------------------------------------------------------------------
_ffmpeg_path: str | None = None


def warm() -> None:
    """Cache the ffmpeg binary path once. Called from orchestrator.warm_all()."""
    global _ffmpeg_path
    _ffmpeg_path = shutil.which("ffmpeg")


def _looks_like_wav(raw: bytes) -> bool:
    """Return True if *raw* starts with a RIFF/WAVE header."""
    return raw[:4] == b"RIFF" and raw[8:12] == b"WAVE"


def to_wav(raw: bytes) -> bytes:
    """Normalize arbitrary browser audio to 16 kHz mono PCM WAV.

    - ffmpeg on PATH: decode whatever came in (webm/opus, mp4/aac, ogg, ...),
      emit canonical 16 kHz mono WAV.
    - ffmpeg absent + bytes already WAV: pass through untouched.
    - ffmpeg absent + bytes NOT WAV: raise a clear error (mis-provisioned env).

    No temp files — ffmpeg reads from stdin and writes to stdout via pipes.
    """
    # Ensure the ffmpeg path is cached (warm() may not have been called yet,
    # e.g. in unit tests that import this module directly).
    ffmpeg = _ffmpeg_path if _ffmpeg_path is not None else shutil.which("ffmpeg")

    if ffmpeg is None:
        # No ffmpeg available — graceful passthrough for genuine WAV.
        if _looks_like_wav(raw):
            return raw
        raise RuntimeError(
            "ffmpeg is not installed and the uploaded audio is not a WAV file. "
            "Install ffmpeg on the system to accept browser-recorded audio "
            "(webm/opus, mp4/aac)."
        )

    # ffmpeg present — transcode to canonical 16 kHz mono WAV via pipes.
    result = subprocess.run(
        [
            ffmpeg, "-hide_banner", "-loglevel", "error",
            "-i", "pipe:0",
            "-ac", "1", "-ar", "16000",
            "-f", "wav", "pipe:1",
        ],
        input=raw,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    )
    return result.stdout