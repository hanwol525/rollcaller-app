"""Tests for the transcode module: browser audio → canonical WAV.

Mirrors the TestPronunciationFallbacks style — exercises the graceful
passthrough and ffmpeg transcode paths without requiring the full ML stack.
"""
from __future__ import annotations

import io
import os
import shutil
import subprocess
import tempfile

import pytest
import soundfile as sf

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
    def test_transcodes_non_canonical_wav_to_riff(self):
        """Feed a non-canonical WAV (48 kHz stereo) and confirm to_wav
        transcodes it to canonical 16 kHz mono WAV with real audio samples.
        Uses WAV so ffmpeg can auto-detect the format from the temp file."""
        non_canonical = subprocess.run(
            [
                "ffmpeg", "-hide_banner", "-loglevel", "error",
                "-f", "lavfi", "-i", "sine=frequency=440:duration=0.1",
                "-ar", "48000", "-ac", "2", "-f", "wav", "pipe:1",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
        ).stdout

        transcode._ffmpeg_path = None
        result = transcode.to_wav(non_canonical)
        assert result[:4] == b"RIFF"
        assert result[8:12] == b"WAVE"
        audio, sr = sf.read(io.BytesIO(result))
        print(f"non_canonical_wav -> {len(audio)} samples @ {sr}Hz")
        assert len(audio) > 0
        assert sr == 16000

    @pytest.mark.skipif(
        shutil.which("ffmpeg") is None,
        reason="ffmpeg not installed",
    )
    def test_transcodes_webm_to_wav(self):
        """End-to-end: synthesize a webm/opus clip, confirm to_wav → WAV with
        real audio samples (not just a RIFF header)."""
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
        audio, sr = sf.read(io.BytesIO(result))
        print(f"webm/opus -> {len(audio)} samples @ {sr}Hz")
        assert len(audio) > 0

    @pytest.mark.skipif(
        shutil.which("ffmpeg") is None,
        reason="ffmpeg not installed",
    )
    def test_transcodes_m4a_aac_to_wav_nonzero_samples(self):
        """Regression: M4A/AAC (Safari's recording format) must transcode to
        WAV with real audio samples.  Pipes produced an empty clip (valid RIFF
        header, zero samples); temp files fix both the moov-atom seek and the
        WAV data-size backfill.  This is the test that catches the pipe bug.

        M4A/MP4 is itself non-streamable (moov atom needs a seekable output),
        so the fixture must be synthesized to a temp file, not piped."""
        m4a_path: str | None = None
        try:
            with tempfile.NamedTemporaryFile(suffix=".m4a", delete=False) as tmp:
                m4a_path = tmp.name

            subprocess.run(
                [
                    "ffmpeg", "-hide_banner", "-loglevel", "error",
                    "-f", "lavfi", "-i", "sine=frequency=440:duration=1.0",
                    "-c:a", "aac", "-b:a", "64k",
                    "-y", m4a_path,
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=True,
            )

            with open(m4a_path, "rb") as f:
                m4a = f.read()

            transcode._ffmpeg_path = None
            result = transcode.to_wav(m4a)
            assert result[:4] == b"RIFF"
            assert result[8:12] == b"WAVE"
            audio, sr = sf.read(io.BytesIO(result))
            print(f"m4a/aac -> {len(audio)} samples @ {sr}Hz")
            assert len(audio) > 0, (
                f"transcoded M4A decoded to {len(audio)} samples — "
                "expected thousands (pipe bug regression)"
            )
        finally:
            if m4a_path is not None and os.path.exists(m4a_path):
                os.unlink(m4a_path)

    @pytest.mark.skipif(
        shutil.which("ffmpeg") is None,
        reason="ffmpeg not installed",
    )
    def test_wav_passes_through_even_with_ffmpeg(self, wav_bytes):
        """When ffmpeg IS present, genuine WAV should still work (gets
        re-transcoded to 16 kHz mono, but output is valid WAV with samples)."""
        transcode._ffmpeg_path = None
        result = transcode.to_wav(wav_bytes)
        assert result[:4] == b"RIFF"
        assert result[8:12] == b"WAVE"
        audio, sr = sf.read(io.BytesIO(result))
        print(f"wav -> {len(audio)} samples @ {sr}Hz")
        assert len(audio) > 0


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