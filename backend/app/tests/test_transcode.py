"""Tests for the transcode module: browser audio → canonical WAV.

Mirrors the TestPronunciationFallbacks style — exercises the graceful
passthrough and ffmpeg transcode paths without requiring the full ML stack.
"""
from __future__ import annotations

import shutil
import subprocess

import pytest

from app.pronunciation import transcode


# ---------------------------------------------------------------------------
# _looks_like_wav
# ---------------------------------------------------------------------------
class TestLooksLikeWav:
    def test_genuine_wav_returns_true(self, wav_bytes):
        assert transcode._looks_like_wav(wav_bytes) is True

    def test_bare_riff_wave_header(self):
        raw = b"RIFF\x00\x00\x00\x00WAVEfmt "
        assert transcode._looks_like_wav(raw) is True

    def test_webm_magic_returns_false(self):
        # webm/matroska magic bytes
        raw = b"\x1aE\xdf\xa3\x01\x00\x00\x00"
        assert transcode._looks_like_wav(raw) is False

    def test_junk_returns_false(self):
        assert transcode._looks_like_wav(b"\x00\x01\x02\x03\x04\x05") is False

    def test_empty_bytes_returns_false(self):
        assert transcode._looks_like_wav(b"") is False

    def test_short_bytes_returns_false(self):
        # Less than 12 bytes — can't contain RIFF + WAVE
        assert transcode._looks_like_wav(b"RIFF") is False


# ---------------------------------------------------------------------------
# to_wav — graceful passthrough (no ffmpeg)
# ---------------------------------------------------------------------------
class TestToWavPassthrough:
    """When ffmpeg is absent, WAV passes through and non-WAV raises."""

    def test_wav_passes_through_unchanged(self, wav_bytes, monkeypatch):
        monkeypatch.setattr(transcode.shutil, "which", lambda _: None)
        transcode._ffmpeg_path = None  # reset cached path
        result = transcode.to_wav(wav_bytes)
        assert result == wav_bytes
        assert result is wav_bytes or result == wav_bytes

    def test_non_wav_raises_without_ffmpeg(self, monkeypatch):
        monkeypatch.setattr(transcode.shutil, "which", lambda _: None)
        transcode._ffmpeg_path = None
        webm = b"\x1aE\xdf\xa3" + b"\x00" * 50
        with pytest.raises(RuntimeError, match="ffmpeg"):
            transcode.to_wav(webm)


# ---------------------------------------------------------------------------
# to_wav — ffmpeg transcode (skipped if ffmpeg not installed)
# ---------------------------------------------------------------------------
class TestToWavTranscode:
    @pytest.mark.skipif(
        shutil.which("ffmpeg") is None,
        reason="ffmpeg not installed",
    )
    def test_transcodes_non_wav_to_riff(self):
        """Synthesize a tiny non-WAV input (raw sine via ffmpeg lavfi) and
        confirm to_wav produces canonical WAV (RIFF header)."""
        # Generate 0.1s of raw PCM (not WAV) — ffmpeg lavfi sine → raw s16le.
        raw_pcm = subprocess.run(
            [
                "ffmpeg", "-hide_banner", "-loglevel", "error",
                "-f", "lavfi", "-i", "sine=frequency=440:duration=0.1",
                "-f", "s16le", "-ac", "1", "-ar", "16000", "pipe:1",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
        ).stdout

        # Reset cache so to_wav picks up the real ffmpeg on PATH
        transcode._ffmpeg_path = None
        result = transcode.to_wav(raw_pcm)
        assert result[:4] == b"RIFF"
        assert result[8:12] == b"WAVE"

    @pytest.mark.skipif(
        shutil.which("ffmpeg") is None,
        reason="ffmpeg not installed",
    )
    def test_transcodes_webm_to_wav(self):
        """End-to-end: synthesize a webm/opus clip, confirm to_wav → WAV."""
        webm = subprocess.run(
            [
                "ffmpeg", "-hide_banner", "-loglevel", "error",
                "-f", "lavfi", "-i", "sine=frequency=440:duration=0.1",
                "-c:a", "libopus", "-f", "webm", "pipe:1",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
        ).stdout

        transcode._ffmpeg_path = None
        result = transcode.to_wav(webm)
        assert result[:4] == b"RIFF"
        assert result[8:12] == b"WAVE"

    @pytest.mark.skipif(
        shutil.which("ffmpeg") is None,
        reason="ffmpeg not installed",
    )
    def test_wav_passes_through_even_with_ffmpeg(self, wav_bytes):
        """When ffmpeg IS present, genuine WAV should still work (gets
        re-transcoded to 16 kHz mono, but output is valid WAV)."""
        transcode._ffmpeg_path = None
        result = transcode.to_wav(wav_bytes)
        assert result[:4] == b"RIFF"
        assert result[8:12] == b"WAVE"


# ---------------------------------------------------------------------------
# warm()
# ---------------------------------------------------------------------------
class TestWarm:
    def test_warm_caches_path(self, monkeypatch):
        monkeypatch.setattr(transcode.shutil, "which", lambda _: "/usr/bin/ffmpeg")
        transcode.warm()
        assert transcode._ffmpeg_path == "/usr/bin/ffmpeg"

    def test_warm_caches_none_when_absent(self, monkeypatch):
        monkeypatch.setattr(transcode.shutil, "which", lambda _: None)
        transcode.warm()
        assert transcode._ffmpeg_path is None