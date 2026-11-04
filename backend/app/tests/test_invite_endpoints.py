"""Tests for /invite/{token} endpoints: self, recording, preview, confirm.

The acceptance criterion this file was written against — "a WAV gets POSTed
successfully **with a non-null ipa**, and ipa_text and ipa_source are both
set" — was retracted. A recording is now stored and the status advances even
when recognition yields nothing, so a `recorded` row with no IPA is a legal
outcome rather than a failure. What still holds is the *coupling*: the three
IPA fields only ever move together, and they move with the recording's
provenance (see test_an_upload_moves_the_ipa_with_its_provenance for the
whole table).
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from app.config import settings
from app.storage import storage
from app.tests._deps import KOKORO_AVAILABLE


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

    def test_get_self_returns_space_name(self, client, make_participant):
        """GET /invite/{token} includes the space/event name for participant screens."""
        space, p = make_participant()
        resp = client.get(f"/invite/{p['invite_token']}")
        assert resp.status_code == 200
        assert resp.json()["space_name"] == "Test Space"

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
        """Core acceptance: WAV posted -> blob stored, status='recorded'.

        The IPA half is now conditional. Where wav2vec2 is unavailable -- this
        venv, since `transformers` needs a torch with no macOS x86_64 wheel --
        the recording is still kept and ipa_text/ipa_source stay None/None,
        the invariant's named "no pronunciation received" state. The upload
        used to 422 here, and before that it stored an invented `rec###`.
        """
        space, p = make_participant(name="Alice Lee")

        resp = client.post(
            f"/invite/{p['invite_token']}/recording",
            files={"file": ("recording.wav", wav_bytes, "audio/wav")},
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()

        # Status switched to "recorded" -- audio exists, whatever came of it.
        assert data["status"] == "recorded"

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

        # The invariant holds either way: both None or both filled.
        assert (participant["ipa_text"] is None) == (participant["ipa_source"] is None)
        if participant["ipa_text"] is None:
            # No recognizer: nothing is claimed about the pronunciation.
            assert participant["ipa_source"] is None
            assert participant["ipa_confirmed"] is False
        else:
            assert participant["ipa_source"] == "recognized"
            assert participant["ipa_confirmed"] is True

        # Verify blob is actually on disk
        blob_path = Path(settings.storage_fs_root) / participant["recording_key"]
        assert blob_path.exists()
        assert blob_path.stat().st_size > 0

    def test_unrecognized_recording_still_gets_a_ceremony_clip(
        self, client, authed_client, make_participant, wav_bytes, monkeypatch
    ):
        """The whole point of the change, end to end.

        Before: the upload stored an invented `rec###` as "recognized", the
        TTS guard refused it for containing digits, and that participant got
        no clip at all. Now the recording is kept with no IPA claimed, and
        Prep Clips falls through to g2p -- so a real pronunciation of their
        name reaches the ceremony.
        """
        from app.pronunciation import orchestrator

        space, p = make_participant(name="Alice Lee")

        resp = client.post(
            f"/invite/{p['invite_token']}/recording",
            files={"file": ("recording.wav", wav_bytes, "audio/wav")},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["ipa_text"] is None     # nothing claimed

        monkeypatch.setattr(orchestrator, "render_clip", lambda *a, **k: b"RIFF")
        resp = authed_client.post(f"/spaces/{space['id']}/render")
        assert resp.status_code == 200, resp.text
        assert resp.json()["failures"] == [], resp.json()
        assert resp.json()["rendered"] == 1

        roster = authed_client.get(f"/spaces/{space['id']}/participants").json()
        participant = [x for x in roster if x["id"] == p["id"]][0]
        assert participant["clip_key"] is not None       # the clip exists
        assert participant["ipa_source"] == "g2p"        # honest provenance
        assert participant["recording_key"] is not None  # recording kept

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

    @pytest.mark.parametrize(
        ("prior_source", "recognizes", "expected"),
        [
            ("recognized", "ˈnjuː", ("ˈnjuː", "recognized", True)),
            ("recognized", None, (None, None, False)),
            ("manual", "ˈnjuː", ("ˈmænjuəl", "manual", True)),
            ("manual", None, ("ˈmænjuəl", "manual", True)),
            ("g2p", "ˈnjuː", ("ˈnjuː", "recognized", True)),
            ("g2p", None, ("bˈɑːb", "g2p", True)),
        ],
        ids=[
            "recognized-is-replaced",
            "recognized-is-cleared",
            "manual-survives-a-readable-take",
            "manual-survives-an-unreadable-take",
            "g2p-is-replaced",
            "g2p-survives-an-unreadable-take",
        ],
    )
    def test_an_upload_moves_the_ipa_with_its_provenance(
        self, authed_client, make_participant, wav_bytes, monkeypatch,
        prior_source, recognizes, expected,
    ):
        """The whole decision table, both directions.

        Two defects live here. A re-record that read nothing used to keep the
        previous take's IPA while deleting the blob that produced it, leaving
        ipa_source='recognized' describing a recording that no longer existed.
        And a re-record that read something used to overwrite a hand-typed
        pronunciation and stamp the machine's as confirmed.

        Recognition is monkeypatched in **both** directions on purpose. Letting
        it fail by itself -- `transformers` is absent on this machine -- would
        make every "recognized" prior state unreachable and the test would pass
        against the bug it exists for. That is the trap CLAUDE.md's "don't let
        a test depend on a dependency being absent" Gotcha records.

        The `manual` rows are not optional coverage: without them, "always
        overwrite" and "always clear" both pass, and the carve-out gets deleted
        by the next person who simplifies the branch.
        """
        from app.pronunciation import orchestrator

        space, p = make_participant(name="Alice Lee")
        token, pid = p["invite_token"], p["id"]

        def row():
            rows = authed_client.get(f"/spaces/{space['id']}/participants").json()
            return [x for x in rows if x["id"] == pid][0]

        def upload():
            resp = authed_client.post(
                f"/invite/{token}/recording",
                files={"file": ("r.wav", wav_bytes, "audio/wav")},
            )
            assert resp.status_code == 200, resp.text

        monkeypatch.setattr(orchestrator, "render_clip", lambda *a, **k: b"RIFF")

        # Reach each prior state through a real route, never a direct DB write.
        if prior_source == "recognized":
            monkeypatch.setattr(orchestrator, "recognize_recording", lambda wav: "ˈoʊld")
            upload()
        elif prior_source == "manual":
            resp = authed_client.post(
                f"/invite/{token}/ipa/confirm",
                json={"ipa": "ˈmænjuəl", "is_edit": True},
            )
            assert resp.status_code == 200, resp.text
        elif prior_source == "g2p":
            monkeypatch.setattr(orchestrator, "g2p_name", lambda name: "bˈɑːb")
            resp = authed_client.post(f"/spaces/{space['id']}/render")
            assert resp.status_code == 200, resp.text
        assert row()["ipa_source"] == prior_source, "prior state not set up"

        key_before = row()["recording_key"]

        # Record again, with recognition forced either way.
        monkeypatch.setattr(orchestrator, "recognize_recording", lambda wav: recognizes)
        upload()

        after = row()
        assert (
            after["ipa_text"], after["ipa_source"], after["ipa_confirmed"]
        ) == expected
        # The recording is kept and the status advances whatever came of it.
        assert after["status"] == "recorded"
        assert after["recording_key"] is not None
        if key_before is not None:
            # The blob that the old 'recognized' IPA described is now gone --
            # which is exactly why keeping that IPA was a lie.
            assert after["recording_key"] != key_before


class TestPreviewIPA:
    @pytest.mark.integration
    @pytest.mark.skipif(
        not KOKORO_AVAILABLE,
        reason="kokoro not installed",
    )
    def test_preview_returns_wav(self, client, make_participant):
        space, p = make_participant()
        resp = client.post(
            f"/invite/{p['invite_token']}/ipa/preview",
            json={"ipa": "/həloʊ/"},
        )
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "audio/wav"
        assert len(resp.content) > 0

    @pytest.mark.integration
    @pytest.mark.skipif(
        not KOKORO_AVAILABLE,
        reason="kokoro not installed",
    )
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
    @pytest.mark.integration
    @pytest.mark.skipif(
        not KOKORO_AVAILABLE,
        reason="kokoro not installed",
    )
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

    @pytest.mark.integration
    @pytest.mark.skipif(
        not KOKORO_AVAILABLE,
        reason="kokoro not installed",
    )
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

    @pytest.mark.integration
    @pytest.mark.skipif(
        not KOKORO_AVAILABLE,
        reason="kokoro not installed",
    )
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