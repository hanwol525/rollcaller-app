"""Tests for /invite/{token} endpoints: self, recording, preview, confirm.

This file covers the ACCEPTANCE CRITERIA:
  A WAV file gets POSTed to /invite/{token}/recording successfully with a
  non-null ipa, the blob gets stored, the status switches to "recorded", and
  ipa_text and ipa_source are both set (never independently).
"""
from __future__ import annotations

import os
from pathlib import Path

from app.config import settings
from app.storage import storage


class TestGetSelf:
    def test_get_self_valid_token(self, client, make_participant):
        space, p = make_participant(name="Alice")
        resp = client.get(f"/invite/{p['invite_token']}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "Alice"
        assert data["status"] == "invited"
        assert data["ipa_text"] is None
        assert data["ipa_confirmed"] is False

    def test_get_self_bad_token_returns_404(self, client):
        resp = client.get("/invite/invalid-token-xxx")
        assert resp.status_code == 404

    def test_get_self_no_auth_required(self, client, make_participant):
        """Invite routes should work with NO login/session."""
        space, p = make_participant()
        resp = client.get(f"/invite/{p['invite_token']}")
        assert resp.status_code == 200


class TestUploadRecording:
    """ACCEPTANCE CRITERIA tests."""

    def test_recording_success_acceptance_criteria(
        self, client, make_participant, wav_bytes
    ):
        """Core acceptance: WAV posted -> non-null ipa, blob stored,
        status='recorded', ipa_text+ipa_source both set."""
        space, p = make_participant(name="Alice Lee")

        resp = client.post(
            f"/invite/{p['invite_token']}/recording",
            files={"file": ("recording.wav", wav_bytes, "audio/wav")},
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()

        # Status switched to "recorded"
        assert data["status"] == "recorded"

        # IPA is non-null
        assert data["ipa_text"] is not None
        assert len(data["ipa_text"]) > 0

        # ipa_confirmed is True (recognized requires it per invariant)
        assert data["ipa_confirmed"] is True

        # Verify the blob was stored — check the recording_key in the DB
        # via the organizer roster endpoint
        # First we need to login as organizer
        client.post(
            "/auth/login",
            json={"username": "organizer", "password": "testpass123"},
        )
        roster = client.get(f"/spaces/{space['id']}/participants").json()
        participant = [x for x in roster if x["id"] == p["id"]][0]
        assert participant["recording_key"] is not None
        assert participant["recording_key"]  # non-empty string

        # ipa_text and ipa_source both set (never independently)
        assert participant["ipa_text"] is not None
        assert participant["ipa_source"] == "recognized"
        # The invariant: both None or both filled
        assert (participant["ipa_text"] is None) == (participant["ipa_source"] is None)

        # Verify blob is actually on disk
        blob_path = Path(settings.storage_fs_root) / participant["recording_key"]
        assert blob_path.exists()
        assert blob_path.stat().st_size > 0

    def test_recording_bad_token_404(self, client, wav_bytes):
        resp = client.post(
            "/invite/bad-token/recording",
            files={"file": ("recording.wav", wav_bytes, "audio/wav")},
        )
        assert resp.status_code == 404

    def test_recording_empty_file_400(self, client, make_participant):
        space, p = make_participant()
        resp = client.post(
            f"/invite/{p['invite_token']}/recording",
            files={"file": ("empty.wav", b"", "audio/wav")},
        )
        assert resp.status_code == 400

    def test_recording_no_auth_required(self, client, make_participant, wav_bytes):
        """Recording upload works with no login — token is the auth."""
        space, p = make_participant()
        resp = client.post(
            f"/invite/{p['invite_token']}/recording",
            files={"file": ("recording.wav", wav_bytes, "audio/wav")},
        )
        assert resp.status_code == 200

    def test_recording_replaces_previous(self, client, make_participant, wav_bytes):
        """Uploading a second recording replaces the first blob."""
        space, p = make_participant()

        # First upload
        resp1 = client.post(
            f"/invite/{p['invite_token']}/recording",
            files={"file": ("r1.wav", wav_bytes, "audio/wav")},
        )
        assert resp1.status_code == 200

        # Login as organizer to check recording_key
        client.post(
            "/auth/login",
            json={"username": "organizer", "password": "testpass123"},
        )
        roster = client.get(f"/spaces/{space['id']}/participants").json()
        key1 = [x for x in roster if x["id"] == p["id"]][0]["recording_key"]

        # Second upload
        # Logout first to avoid confusion (invite routes don't need auth anyway)
        client.post("/auth/logout")
        resp2 = client.post(
            f"/invite/{p['invite_token']}/recording",
            files={"file": ("r2.wav", wav_bytes, "audio/wav")},
        )
        assert resp2.status_code == 200

        client.post(
            "/auth/login",
            json={"username": "organizer", "password": "testpass123"},
        )
        roster = client.get(f"/spaces/{space['id']}/participants").json()
        key2 = [x for x in roster if x["id"] == p["id"]][0]["recording_key"]

        # The old blob should be deleted, new one created (different key)
        assert key1 != key2
        old_path = Path(settings.storage_fs_root) / key1
        new_path = Path(settings.storage_fs_root) / key2
        assert not old_path.exists()  # old deleted
        assert new_path.exists()       # new exists


class TestPreviewIPA:
    def test_preview_returns_wav(self, client, make_participant):
        space, p = make_participant()
        resp = client.post(
            f"/invite/{p['invite_token']}/ipa/preview",
            json={"ipa": "/həloʊ/"},
        )
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "audio/wav"
        assert len(resp.content) > 0

    def test_preview_does_not_store(self, client, make_participant):
        """CRITICAL: preview must NOT write anything to storage."""
        space, p = make_participant()
        blob_dir = Path(settings.storage_fs_root)

        files_before = set(blob_dir.iterdir()) if blob_dir.exists() else set()

        client.post(
            f"/invite/{p['invite_token']}/ipa/preview",
            json={"ipa": "/tɛst/"},
        )

        files_after = set(blob_dir.iterdir()) if blob_dir.exists() else set()
        # No new files should have been created
        assert files_after == files_before, "Preview wrote to storage!"

    def test_preview_bad_token_404(self, client):
        resp = client.post(
            "/invite/bad-token/ipa/preview",
            json={"ipa": "/tɛst/"},
        )
        assert resp.status_code == 404


class TestConfirmIPA:
    def test_confirm_sets_confirmed_and_clip(self, client, make_participant):
        space, p = make_participant()

        resp = client.post(
            f"/invite/{p['invite_token']}/ipa/confirm",
            json={"ipa": "/ælɪs/", "is_edit": True},
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["status"] == "confirmed"
        assert data["ipa_confirmed"] is True
        assert data["ipa_text"] == "/ælɪs/"

        # Verify clip was stored
        client.post(
            "/auth/login",
            json={"username": "organizer", "password": "testpass123"},
        )
        roster = client.get(f"/spaces/{space['id']}/participants").json()
        participant = [x for x in roster if x["id"] == p["id"]][0]
        assert participant["clip_key"] is not None
        assert participant["ipa_source"] == "manual"  # human edit

        clip_path = Path(settings.storage_fs_root) / participant["clip_key"]
        assert clip_path.exists()

    def test_confirm_without_edit_keeps_prior_source(
        self, client, make_participant, wav_bytes
    ):
        """If confirming an unchanged IPA (no edit), keep the prior source."""
        space, p = make_participant()

        # First, upload a recording to set ipa_source='recognized'
        resp = client.post(
            f"/invite/{p['invite_token']}/recording",
            files={"file": ("r.wav", wav_bytes, "audio/wav")},
        )
        assert resp.status_code == 200
        recognized_ipa = resp.json()["ipa_text"]

        # Confirm WITHOUT editing (is_edit=False) — should keep 'recognized'
        resp = client.post(
            f"/invite/{p['invite_token']}/ipa/confirm",
            json={"ipa": recognized_ipa, "is_edit": False},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "confirmed"

        # Check source is still 'recognized'
        client.post(
            "/auth/login",
            json={"username": "organizer", "password": "testpass123"},
        )
        roster = client.get(f"/spaces/{space['id']}/participants").json()
        participant = [x for x in roster if x["id"] == p["id"]][0]
        assert participant["ipa_source"] == "recognized"

    def test_confirm_with_edit_sets_manual(self, client, make_participant, wav_bytes):
        """A human edit to the IPA sets ipa_source='manual'."""
        space, p = make_participant()

        # Upload recording first
        client.post(
            f"/invite/{p['invite_token']}/recording",
            files={"file": ("r.wav", wav_bytes, "audio/wav")},
        )

        # Confirm with a different IPA (human edit)
        resp = client.post(
            f"/invite/{p['invite_token']}/ipa/confirm",
            json={"ipa": "/totally_different/", "is_edit": True},
        )
        assert resp.status_code == 200

        client.post(
            "/auth/login",
            json={"username": "organizer", "password": "testpass123"},
        )
        roster = client.get(f"/spaces/{space['id']}/participants").json()
        participant = [x for x in roster if x["id"] == p["id"]][0]
        assert participant["ipa_source"] == "manual"
        assert participant["ipa_text"] == "/totally_different/"

    def test_confirm_bad_token_404(self, client):
        resp = client.post(
            "/invite/bad-token/ipa/confirm",
            json={"ipa": "/x/", "is_edit": True},
        )
        assert resp.status_code == 404