"""Tests for /spaces/{id}/render and /spaces/{id}/ceremony endpoints."""
from __future__ import annotations

from pathlib import Path

import pytest

from app.config import settings
from app.tests._deps import KOKORO_AVAILABLE


class TestRenderCeremony:
    @pytest.mark.integration
    @pytest.mark.skipif(
        not KOKORO_AVAILABLE,
        reason="kokoro not installed",
    )
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

    @pytest.mark.integration
    @pytest.mark.skipif(
        not KOKORO_AVAILABLE,
        reason="kokoro not installed",
    )
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

    def test_render_prefers_stored_ipa_even_when_unconfirmed(
        self, authed_client, make_space, db_engine, monkeypatch
    ):
        """Pins a **defensive** branch, against a state no HTTP route reaches.

        _render_one prefers any stored pronunciation over regenerating one and
        deliberately does not consult ipa_confirmed. Nothing can actually
        produce unconfirmed-with-text: every writer sets ipa_confirmed=True
        (ceremony.py, and invite.py in both the recognize and confirm paths),
        ParticipantUpdate exposes only name/email/position, and AUDIT.md §4.6
        records the state as one no route can produce. So the state is built
        here by direct DB write, and that is the whole value of the test --
        it guards the branch, it does not demonstrate a reachable bug.

        It replaces test_render_g2p_overrides_recording_if_unconfirmed, which
        claimed to cover "a recording left unconfirmed" -- impossible, since
        recognition always confirms -- and whose body never uploaded a
        recording, so it only exercised the no-IPA path that
        test_render_creates_clip_for_every_participant already covers.
        """
        from sqlmodel import Session, select

        from app.models import Participant
        from app.pronunciation import orchestrator

        space = make_space()
        resp = authed_client.post(
            f"/spaces/{space['id']}/participants", json=[{"name": "Alice"}],
        )
        pid = resp.json()[0]["id"]

        with Session(db_engine) as s:
            p = s.exec(select(Participant).where(Participant.id == pid)).one()
            p.ipa_text = "ˈmænjuəl"
            p.ipa_source = "manual"
            p.ipa_confirmed = False
            s.add(p)
            s.commit()

        seen: list[str | None] = []
        monkeypatch.setattr(
            orchestrator, "g2p_name",
            lambda name: pytest.fail("stored IPA must win over regenerating"),
        )
        monkeypatch.setattr(
            orchestrator, "render_clip",
            lambda name, ipa_text=None: seen.append(ipa_text) or b"RIFF",
        )

        resp = authed_client.post(f"/spaces/{space['id']}/render")
        assert resp.status_code == 200
        assert seen == ["ˈmænjuəl"], seen

        p = authed_client.get(f"/spaces/{space['id']}/participants").json()[0]
        assert p["ipa_source"] == "manual"      # provenance untouched
        assert p["ipa_confirmed"] is False
        assert p["clip_key"] is not None

    def test_render_reports_failures_and_commits_the_rest(
        self, authed_client, make_space, monkeypatch
    ):
        """One bad name must not discard the whole roster's work.

        Fakes the failure instead of needing Kokoro: 3 of this class's 13
        render tests are @integration and run nowhere, and those 3 are the
        only ones that exercise the real pipeline, so this path would
        otherwise have no local coverage at all.
        """
        from app.pronunciation import orchestrator

        space = make_space()
        authed_client.post(
            f"/spaces/{space['id']}/participants",
            json=[{"name": "Alice"}, {"name": "Boom"}, {"name": "Carol"}],
        )
        monkeypatch.setattr(orchestrator, "g2p_name", lambda name: "bˈɑːb")

        def _render(name, ipa_text=None):
            if name == "Boom":
                raise RuntimeError("Kokoro produced no audio")
            return b"RIFF"

        monkeypatch.setattr(orchestrator, "render_clip", _render)

        resp = authed_client.post(f"/spaces/{space['id']}/render")
        assert resp.status_code == 200
        body = resp.json()
        assert body["rendered"] == 2
        assert [f["name"] for f in body["failures"]] == ["Boom"]
        assert "RuntimeError" in body["failures"][0]["reason"]

        roster = {
            p["name"]: p
            for p in authed_client.get(f"/spaces/{space['id']}/participants").json()
        }
        assert roster["Alice"]["clip_key"] is not None
        assert roster["Carol"]["clip_key"] is not None
        # The regression guard. Before phase 2 moved off the worker thread, a
        # failed render still stamped ipa_source='g2p' and ipa_confirmed=True
        # onto the row -- a state nothing downstream could tell from success.
        assert roster["Boom"]["clip_key"] is None
        assert roster["Boom"]["ipa_text"] is None
        assert roster["Boom"]["ipa_source"] is None
        assert roster["Boom"]["ipa_confirmed"] is False

    def test_render_refuses_garbage_ipa_rather_than_voicing_it(
        self, authed_client, make_space, monkeypatch
    ):
        """A refused pronunciation means no clip, not a clip from spelling."""
        from app.pronunciation import orchestrator

        space = make_space()
        authed_client.post(
            f"/spaces/{space['id']}/participants", json=[{"name": "Alice Lee"}],
        )
        # rec044-shaped: the digit rule fires, exactly as it would on a
        # recognizer placeholder that reached the renderer.
        monkeypatch.setattr(orchestrator, "g2p_name", lambda name: "rec044")
        monkeypatch.setattr(
            orchestrator, "render_clip",
            lambda *a, **k: pytest.fail("a refused IPA must never be rendered"),
        )

        resp = authed_client.post(f"/spaces/{space['id']}/render")
        assert resp.status_code == 200
        body = resp.json()
        assert body["rendered"] == 0
        assert [f["name"] for f in body["failures"]] == ["Alice Lee"]
        assert "digit" in body["failures"][0]["reason"]

        p = authed_client.get(f"/spaces/{space['id']}/participants").json()[0]
        assert p["clip_key"] is None

    def test_a_refusal_withdraws_the_clip_the_ceremony_would_have_played(
        self, authed_client, make_space, db_engine, monkeypatch
    ):
        """The case the test above cannot reach: a refused participant who
        already holds a clip.

        That one asserts `clip_key is None` for someone who never had a clip,
        so it cannot fail. The clip a refused participant actually holds came
        from confirm_ipa, where synthesize *degrades* instead of refusing --
        so it is a reading of their spelling, the one thing the refusal exists
        to prevent. Leaving it meant GET /ceremony kept serving it while the
        render report said they had no pronunciation.

        The refusable IPA is set by direct DB write rather than through
        /ipa/confirm because that route is about to reject it (Unit 3b), which
        makes this a state no route can produce -- the same justification
        test_render_prefers_stored_ipa_even_when_unconfirmed records.
        """
        from sqlmodel import Session, select

        from app.models import Participant
        from app.pronunciation import orchestrator

        space = make_space()
        resp = authed_client.post(
            f"/spaces/{space['id']}/participants", json=[{"name": "Alice Lee"}],
        )
        pid = resp.json()[0]["id"]

        # 1. A clean render, so there is a clip for the refusal to contradict.
        monkeypatch.setattr(orchestrator, "g2p_name", lambda name: "bˈɑːb")
        monkeypatch.setattr(orchestrator, "render_clip", lambda *a, **k: b"RIFF")
        assert authed_client.post(f"/spaces/{space['id']}/render").status_code == 200
        clip = authed_client.get(
            f"/spaces/{space['id']}/participants"
        ).json()[0]["clip_key"]
        assert clip is not None
        assert (Path(settings.storage_fs_root) / clip).exists()
        roster = authed_client.get(f"/spaces/{space['id']}/ceremony").json()["roster"]
        assert roster[0]["clip_url"] is not None

        # 2. The stored pronunciation becomes one the guard refuses.
        with Session(db_engine) as s:
            p = s.exec(select(Participant).where(Participant.id == pid)).one()
            p.ipa_text = "rec044"
            s.add(p)
            s.commit()

        # 3. Re-render. Kokoro must not be reached, and the clip must go.
        monkeypatch.setattr(
            orchestrator, "render_clip",
            lambda *a, **k: pytest.fail("a refused IPA must never be rendered"),
        )
        body = authed_client.post(f"/spaces/{space['id']}/render").json()
        assert body["rendered"] == 0
        assert [f["name"] for f in body["failures"]] == ["Alice Lee"]

        after = authed_client.get(f"/spaces/{space['id']}/participants").json()[0]
        assert after["clip_key"] is None
        assert not (Path(settings.storage_fs_root) / clip).exists()
        roster = authed_client.get(f"/spaces/{space['id']}/ceremony").json()["roster"]
        assert roster[0]["clip_url"] is None, "the ceremony would still play it"
        # Provenance survives, so one more Prep Clips is a full recovery.
        assert after["ipa_text"] == "rec044"
        assert after["ipa_source"] == "g2p"

    def test_a_render_that_raises_keeps_the_clip_it_already_had(
        self, authed_client, make_space, monkeypatch
    ):
        """The asymmetry, guarded so it does not read as an oversight.

        A refusal is a deterministic judgement that will repeat, so the stale
        clip is actively wrong. A raise is an unknown and usually transient --
        a Kokoro OOM under four workers -- and silence at a ceremony is worse
        than a clip that worked on the last run. Deleting on both would be the
        obvious simplification, and it would be wrong.
        """
        from app.pronunciation import orchestrator

        space = make_space()
        authed_client.post(
            f"/spaces/{space['id']}/participants", json=[{"name": "Alice Lee"}],
        )
        monkeypatch.setattr(orchestrator, "g2p_name", lambda name: "bˈɑːb")
        monkeypatch.setattr(orchestrator, "render_clip", lambda *a, **k: b"RIFF")
        authed_client.post(f"/spaces/{space['id']}/render")
        clip = authed_client.get(
            f"/spaces/{space['id']}/participants"
        ).json()[0]["clip_key"]
        assert clip is not None

        def _boom(name, ipa_text=None):
            raise RuntimeError("Kokoro produced no audio")

        monkeypatch.setattr(orchestrator, "render_clip", _boom)
        body = authed_client.post(f"/spaces/{space['id']}/render").json()
        assert [f["name"] for f in body["failures"]] == ["Alice Lee"]

        after = authed_client.get(f"/spaces/{space['id']}/participants").json()[0]
        assert after["clip_key"] == clip, "a transient failure must not cost the clip"
        assert (Path(settings.storage_fs_root) / clip).exists()

    def test_a_failing_save_leaves_the_previous_clip_intact(
        self, authed_client, make_space, monkeypatch
    ):
        """storage.save mints a new key, so the old blob is a separate file."""
        from app.pronunciation import orchestrator
        from app import storage as storage_module

        space = make_space()
        authed_client.post(
            f"/spaces/{space['id']}/participants", json=[{"name": "Alice"}],
        )
        monkeypatch.setattr(orchestrator, "g2p_name", lambda name: "bˈɑːb")
        monkeypatch.setattr(orchestrator, "render_clip", lambda *a, **k: b"RIFF")

        authed_client.post(f"/spaces/{space['id']}/render")
        first = authed_client.get(
            f"/spaces/{space['id']}/participants"
        ).json()[0]["clip_key"]
        assert (Path(settings.storage_fs_root) / first).exists()

        def _boom(data, ext="wav"):
            raise OSError("disk full")

        monkeypatch.setattr(storage_module.storage, "save", _boom)
        with pytest.raises(OSError):
            authed_client.post(f"/spaces/{space['id']}/render")

        # The row still points at the old key, and the old file is still there.
        still = authed_client.get(
            f"/spaces/{space['id']}/participants"
        ).json()[0]["clip_key"]
        assert still == first
        assert (Path(settings.storage_fs_root) / first).exists()

    def test_superseded_clips_are_deleted_only_after_the_commit(
        self, authed_client, make_space, db_engine, monkeypatch
    ):
        """The ordering AUDIT.md §4.3 is really about.

        Saving before deleting is not enough: the delete still ran before
        db.commit(), so a dropped connection rolled back every new clip_key
        while the old blobs were gone -- rows pointing at deleted files. With
        the deletes moved after the commit, a commit failure can only leak.
        db.py's own comment notes managed providers drop idle connections, so
        this is a designed-for failure, not a hypothetical.

        Covers **both** classes of orphan, because they share one list and one
        post-commit loop: a clip superseded by a fresh render (Alice) and a
        clip withdrawn from a refused participant (Refused). The second is
        the one at risk -- deleting it inline looks harmless, since there is
        no new key to roll back, but a commit failure would then leave the row
        pointing at a blob that is already gone. With only Alice here, this
        test could not catch that.
        """
        from sqlmodel import Session, select

        from app.models import Participant
        from app.pronunciation import orchestrator
        from app import storage as storage_module

        space = make_space()
        resp = authed_client.post(
            f"/spaces/{space['id']}/participants",
            json=[{"name": "Alice"}, {"name": "Refused"}],
        )
        refused_id = [p for p in resp.json() if p["name"] == "Refused"][0]["id"]
        monkeypatch.setattr(orchestrator, "g2p_name", lambda name: "bˈɑːb")
        monkeypatch.setattr(orchestrator, "render_clip", lambda *a, **k: b"RIFF")

        authed_client.post(f"/spaces/{space['id']}/render")
        keys = {
            p["name"]: p["clip_key"]
            for p in authed_client.get(f"/spaces/{space['id']}/participants").json()
        }
        assert all(keys.values()), keys

        # Make the second participant refusable, before commit is neutered.
        with Session(db_engine) as s:
            p = s.exec(select(Participant).where(Participant.id == refused_id)).one()
            p.ipa_text = "rec044"
            s.add(p)
            s.commit()

        deleted: list[str] = []
        monkeypatch.setattr(
            storage_module.storage, "delete", lambda key: deleted.append(key),
        )

        def _boom(self):
            raise RuntimeError("server closed the connection unexpectedly")

        monkeypatch.setattr(Session, "commit", _boom)
        with pytest.raises(RuntimeError):
            authed_client.post(f"/spaces/{space['id']}/render")

        # Nothing was deleted, so both blobs the DB still references survive.
        assert deleted == [], deleted
        for name, key in keys.items():
            assert (Path(settings.storage_fs_root) / key).exists(), name

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

    @pytest.mark.integration
    @pytest.mark.skipif(
        not KOKORO_AVAILABLE,
        reason="kokoro not installed",
    )
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
    def test_both_read_models_say_why_a_name_will_not_be_voiced(
        self, authed_client, make_space, db_engine
    ):
        """`clip_blocked_reason` is computed on read, so it needs a real one.

        The frontend tests for this column feed it fixtures, which proves the
        rendering and nothing about the derivation. Both the roster and the
        ceremony payload have to carry it: the roster so the organizer sees it
        after the per-run message is gone, and the ceremony payload so the
        player can tell "nobody prepped" from "every name was refused".

        Deliberately **not** covered, and asserted here so the limit is
        visible: a participant with no stored IPA reads as unblocked even
        though the render might refuse whatever g2p produces for them.
        Deriving that per row would be an espeak call per name per page load.
        """
        from sqlmodel import Session, select

        from app.models import Participant

        space = make_space()
        resp = authed_client.post(
            f"/spaces/{space['id']}/participants",
            json=[{"name": "Bad"}, {"name": "Good"}, {"name": "Empty"}],
        )
        ids = {p["name"]: p["id"] for p in resp.json()}

        with Session(db_engine) as s:
            for name, ipa in [("Bad", "rec044"), ("Good", "bˈɑːb")]:
                p = s.exec(
                    select(Participant).where(Participant.id == ids[name])
                ).one()
                p.ipa_text, p.ipa_source, p.ipa_confirmed = ipa, "g2p", True
                s.add(p)
            s.commit()

        roster = {
            p["name"]: p
            for p in authed_client.get(f"/spaces/{space['id']}/participants").json()
        }
        assert "digit" in roster["Bad"]["clip_blocked_reason"]
        assert roster["Good"]["clip_blocked_reason"] is None
        assert roster["Empty"]["clip_blocked_reason"] is None   # the stated limit

        items = {
            i["name"]: i
            for i in authed_client.get(f"/spaces/{space['id']}/ceremony").json()["roster"]
        }
        assert "digit" in items["Bad"]["clip_blocked_reason"]
        assert items["Good"]["clip_blocked_reason"] is None

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

    @pytest.mark.integration
    @pytest.mark.skipif(
        not KOKORO_AVAILABLE,
        reason="kokoro not installed",
    )
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