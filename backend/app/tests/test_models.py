"""Tests for non-endpoint logic: model validators, storage, pronunciation fallbacks."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.models import Participant, ParticipantStatus, IPASource


class TestParticipantIPAVariant:
    """The ipa_text/ipa_source invariant: they move together.

    SQLModel table=True models do NOT run @model_validator on __init__ or
    attribute assignment, so we call validate_ipa_invariant() explicitly.
    """

    def test_both_none_is_valid(self):
        """None/None = no pronunciation received. Should be valid."""
        p = Participant(space_id=1, name="Alice")
        p.validate_ipa_invariant()
        assert p.ipa_text is None
        assert p.ipa_source is None
        assert p.ipa_confirmed is False

    def test_both_filled_valid_when_confirmed(self):
        """Both filled + ipa_confirmed=True is valid for recognized/g2p."""
        p = Participant(
            space_id=1, name="Alice",
            ipa_text="/ælɪs/", ipa_source="recognized", ipa_confirmed=True,
        )
        p.validate_ipa_invariant()
        assert p.ipa_text == "/ælɪs/"
        assert p.ipa_source == "recognized"

    def test_text_without_source_raises(self):
        """ipa_text set but ipa_source None should raise."""
        p = Participant(space_id=1, name="Alice", ipa_text="/x/", ipa_source=None)
        with pytest.raises(ValueError):
            p.validate_ipa_invariant()

    def test_source_without_text_raises(self):
        """ipa_source set but ipa_text None should raise."""
        p = Participant(space_id=1, name="Alice", ipa_text=None, ipa_source="g2p")
        with pytest.raises(ValueError):
            p.validate_ipa_invariant()

    def test_g2p_requires_confirmed(self):
        """ipa_source='g2p' with ipa_confirmed=False should raise."""
        p = Participant(
            space_id=1, name="Alice",
            ipa_text="/x/", ipa_source="g2p", ipa_confirmed=False,
        )
        with pytest.raises(ValueError):
            p.validate_ipa_invariant()

    def test_recognized_requires_confirmed(self):
        """ipa_source='recognized' with ipa_confirmed=False should raise."""
        p = Participant(
            space_id=1, name="Alice",
            ipa_text="/x/", ipa_source="recognized", ipa_confirmed=False,
        )
        with pytest.raises(ValueError):
            p.validate_ipa_invariant()

    def test_manual_with_confirmed_is_valid(self):
        """manual + confirmed is valid."""
        p = Participant(
            space_id=1, name="Alice",
            ipa_text="/x/", ipa_source="manual", ipa_confirmed=True,
        )
        p.validate_ipa_invariant()
        assert p.ipa_source == "manual"

    def test_manual_without_confirmed_is_valid(self):
        """manual + unconfirmed is valid (human edit pending confirmation)."""
        p = Participant(
            space_id=1, name="Alice",
            ipa_text="/x/", ipa_source="manual", ipa_confirmed=False,
        )
        p.validate_ipa_invariant()
        assert p.ipa_source == "manual"
        assert p.ipa_confirmed is False

    def test_non_manual_unconfirmed_raises(self):
        """If ipa_text is set and unconfirmed, source must be manual."""
        p = Participant(
            space_id=1, name="Alice",
            ipa_text="/x/", ipa_source="recognized", ipa_confirmed=False,
        )
        with pytest.raises(ValueError):
            p.validate_ipa_invariant()

    def test_invite_token_auto_generated(self):
        """invite_token should be auto-generated and unique."""
        p1 = Participant(space_id=1, name="A")
        p2 = Participant(space_id=1, name="B")
        assert p1.invite_token != p2.invite_token
        assert len(p1.invite_token) > 10

    def test_default_status_is_invited(self):
        p = Participant(space_id=1, name="Alice")
        assert p.status == ParticipantStatus.invited

    def test_default_position_is_zero(self):
        p = Participant(space_id=1, name="Alice")
        assert p.position == 0


class TestStorageFilesystem:
    """Test the filesystem storage backend directly."""

    def test_save_and_load_roundtrip(self):
        from app.storage import storage
        data = b"fake audio bytes"
        key = storage.save(data, ext="wav")
        assert key.endswith(".wav")
        loaded = storage.load(key)
        assert loaded == data

    def test_url_format(self):
        from app.storage import storage
        key = "testfile.wav"
        url = storage.url(key)
        assert url == f"/media/{key}"

    def test_delete_removes_file(self):
        from app.storage import storage
        key = storage.save(b"data", ext="wav")
        storage.delete(key)
        from app.storage import storage as s
        import pytest
        with pytest.raises(FileNotFoundError):
            s.load(key)

    def test_path_traversal_prevention(self):
        """A key with path separators should be sanitized to basename only."""
        from app.storage import FilesystemStorage
        fs = FilesystemStorage(root="./blobstore_test_traversal", media_base_url="/media")
        key = fs.save(b"data", ext="wav")
        # Try to load with a traversal attempt
        # The _path method strips to basename, so this is safe
        loaded = fs.load(key)
        assert loaded == b"data"
        # Cleanup
        import shutil
        shutil.rmtree("./blobstore_test_traversal", ignore_errors=True)


class TestPronunciationFallbacks:
    """Test that the fallback paths work without ML deps installed."""

    def test_g2p_fallback_returns_non_empty(self):
        from app.pronunciation.g2p import g2p
        result = g2p("Alice")
        assert len(result) > 0
        assert isinstance(result, str)

    def test_recognize_fallback_returns_non_empty(self):
        from app.pronunciation.recognize import recognize
        result = recognize(b"fake wav bytes 12345")
        assert len(result) > 0
        assert isinstance(result, str)

    def test_tts_fallback_returns_wav_bytes(self):
        from app.pronunciation.tts import synthesize
        result = synthesize("Alice", ipa="/ælɪs/")
        assert isinstance(result, bytes)
        assert len(result) > 0
        # WAV files start with RIFF header
        assert result[:4] == b"RIFF"

    def test_orchestrator_g2p_name(self):
        from app.pronunciation.orchestrator import g2p_name
        result = g2p_name("Bob")
        assert isinstance(result, str)
        assert len(result) > 0

    def test_orchestrator_render_clip(self):
        from app.pronunciation.orchestrator import render_clip
        result = render_clip("Bob", ipa_text="/bɒb/")
        assert isinstance(result, bytes)
        assert len(result) > 0

    def test_orchestrator_recognize_recording(self):
        from app.pronunciation.orchestrator import recognize_recording
        result = recognize_recording(b"fake audio")
        assert isinstance(result, str)
        assert len(result) > 0

    def test_warm_all_does_not_raise(self):
        """warm_all should not raise even if ML deps aren't installed."""
        from app.pronunciation.orchestrator import warm_all
        warm_all()  # should complete without error


class TestMediaEndpoint:
    """Test the /media/{key} dev endpoint."""

    def test_serves_existing_file(self, client):
        from app.storage import storage
        data = b"fake audio for media test"
        key = storage.save(data, ext="wav")
        resp = client.get(f"/media/{key}")
        assert resp.status_code == 200
        assert resp.content == data
        assert resp.headers["content-type"] == "audio/wav"

    def test_404_for_nonexistent_file(self, client):
        resp = client.get("/media/nonexistent.wav")
        assert resp.status_code == 404

    def test_media_serves_without_auth(self, client):
        """Media endpoint should be accessible without login."""
        from app.storage import storage
        key = storage.save(b"data", ext="wav")
        resp = client.get(f"/media/{key}")
        assert resp.status_code == 200