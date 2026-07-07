"""Tests for /spaces/{id}/render and /spaces/{id}/ceremony endpoints."""
from __future__ import annotations

from pathlib import Path

from app.config import settings


class TestRenderCeremony:
    def test_render_creates_clip_for_every_participant(self, authed_client, make_space):
        """Every participant gets a clip even if they submitted nothing."""
        space = make_space()
        # Add 3 participants, none have submitted recordings
        authed_client.post(
            f"/spaces/{space['id']}/participants",
            json=[{"name": "Alice"}, {"name": "Bob"}, {"name": "Charlie"}],
        )

        resp = authed_client.post(f"/spaces/{space['id']}/render")
        assert resp.status_code == 200
        assert resp.json()["rendered"] == 3

        # Verify every participant has a clip_key
        roster = authed_client.get(f"/spaces/{space['id']}/participants").json()
        for p in roster:
            assert p["clip_key"] is not None
            assert p["ipa_text"] is not None
            # TTS fallback sets ipa_source='g2p'
            assert p["ipa_source"] == "g2p"
            # Verify clip exists on disk
            clip_path = Path(settings.storage_fs_root) / p["clip_key"]
            assert clip_path.exists()

    def test_render_uses_confirmed_ipa_when_available(
        self, authed_client, make_space, wav_bytes
    ):
        """If a participant has confirmed IPA, use it (not g2p fallback)."""
        space = make_space()
        # Add a participant
        resp = authed_client.post(
            f"/spaces/{space['id']}/participants",
            json=[{"name": "Alice"}],
        )
        token = resp.json()[0]["invite_token"]
        pid = resp.json()[0]["id"]

        # Upload a recording (sets ipa_source='recognized')
        authed_client.post(
            f"/invite/{token}/recording",
            files={"file": ("r.wav", wav_bytes, "audio/wav")},
        )

        # Render — should use the recognized IPA, not g2p
        resp = authed_client.post(f"/spaces/{space['id']}/render")
        assert resp.status_code == 200

        roster = authed_client.get(f"/spaces/{space['id']}/participants").json()
        p = [x for x in roster if x["id"] == pid][0]
        # Source should remain 'recognized' since we used the confirmed IPA
        assert p["ipa_source"] == "recognized"
        assert p["clip_key"] is not None

    def test_render_g2p_overrides_recording_if_unconfirmed(
        self, authed_client, make_space
    ):
        """If a participant has a recording but IPA is NOT confirmed,
        the batch renderer uses g2p and sets ipa_source='g2p'."""
        space = make_space()
        resp = authed_client.post(
            f"/spaces/{space['id']}/participants",
            json=[{"name": "Alice"}],
        )
        pid = resp.json()[0]["id"]

        # Don't upload a recording — participant has no IPA at all
        # Render should use g2p
        authed_client.post(f"/spaces/{space['id']}/render")
        roster = authed_client.get(f"/spaces/{space['id']}/participants").json()
        p = [x for x in roster if x["id"] == pid][0]
        assert p["ipa_source"] == "g2p"

    def test_render_requires_auth(self, client):
        resp = client.post("/spaces/1/render")
        assert resp.status_code == 401

    def test_render_nonexistent_space(self, authed_client):
        resp = authed_client.post("/spaces/9999/render")
        assert resp.status_code == 404

    def test_render_empty_roster(self, authed_client, make_space):
        space = make_space()
        resp = authed_client.post(f"/spaces/{space['id']}/render")
        assert resp.status_code == 200
        assert resp.json()["rendered"] == 0

    def test_rerender_replaces_old_clip(self, authed_client, make_space):
        """Re-rendering should delete the old clip and create a new one."""
        space = make_space()
        authed_client.post(
            f"/spaces/{space['id']}/participants",
            json=[{"name": "Alice"}],
        )

        # First render
        authed_client.post(f"/spaces/{space['id']}/render")
        roster = authed_client.get(f"/spaces/{space['id']}/participants").json()
        key1 = roster[0]["clip_key"]

        # Second render
        authed_client.post(f"/spaces/{space['id']}/render")
        roster = authed_client.get(f"/spaces/{space['id']}/participants").json()
        key2 = roster[0]["clip_key"]

        # Old clip deleted, new one created
        old_path = Path(settings.storage_fs_root) / key1
        new_path = Path(settings.storage_fs_root) / key2
        assert not old_path.exists()
        assert new_path.exists()


class TestGetCeremony:
    def test_ceremony_returns_ordered_roster(self, authed_client, make_space):
        space = make_space(name="Graduation", advanced_seconds=8)
        authed_client.post(
            f"/spaces/{space['id']}/participants",
            json=[{"name": "Alice"}, {"name": "Bob"}, {"name": "Charlie"}],
        )

        resp = authed_client.get(f"/spaces/{space['id']}/ceremony")
        assert resp.status_code == 200
        data = resp.json()
        assert data["space_id"] == space["id"]
        assert data["space_name"] == "Graduation"
        assert data["advanced_seconds"] == 8
        assert len(data["roster"]) == 3
        names = [item["name"] for item in data["roster"]]
        assert names == ["Alice", "Bob", "Charlie"]

    def test_ceremony_clip_urls_null_before_render(self, authed_client, make_space):
        space = make_space()
        authed_client.post(
            f"/spaces/{space['id']}/participants",
            json=[{"name": "Alice"}],
        )
        resp = authed_client.get(f"/spaces/{space['id']}/ceremony")
        assert resp.status_code == 200
        assert resp.json()["roster"][0]["clip_url"] is None

    def test_ceremony_clip_urls_populated_after_render(self, authed_client, make_space):
        space = make_space()
        authed_client.post(
            f"/spaces/{space['id']}/participants",
            json=[{"name": "Alice"}],
        )
        authed_client.post(f"/spaces/{space['id']}/render")
        resp = authed_client.get(f"/spaces/{space['id']}/ceremony")
        assert resp.status_code == 200
        clip_url = resp.json()["roster"][0]["clip_url"]
        assert clip_url is not None
        assert clip_url.startswith("/media/")

    def test_ceremony_requires_auth(self, client):
        resp = client.get("/spaces/1/ceremony")
        assert resp.status_code == 401

    def test_ceremony_nonexistent_space(self, authed_client):
        resp = authed_client.get("/spaces/9999/ceremony")
        assert resp.status_code == 404