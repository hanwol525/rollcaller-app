"""Tests for /spaces/{id}/participants endpoints: add, list, update, delete."""
from __future__ import annotations

import io


class TestAddParticipants:
    def test_add_single_participant_json(self, authed_client, make_space):
        space = make_space()
        resp = authed_client.post(
            f"/spaces/{space['id']}/participants",
            json=[{"name": "Alice Lee", "email": "alice@example.com"}],
        )
        assert resp.status_code == 201
        data = resp.json()
        assert len(data) == 1
        p = data[0]
        assert p["name"] == "Alice Lee"
        assert p["email"] == "alice@example.com"
        assert p["status"] == "invited"
        assert p["ipa_text"] is None
        assert p["ipa_source"] is None
        assert p["ipa_confirmed"] is False
        assert "invite_token" in p
        assert p["invite_token"]  # non-empty

    def test_add_multiple_participants_json(self, authed_client, make_space):
        space = make_space()
        resp = authed_client.post(
            f"/spaces/{space['id']}/participants",
            json=[
                {"name": "Alice", "email": "a@x.com"},
                {"name": "Bob", "email": None},
                {"name": "Charlie"},
            ],
        )
        assert resp.status_code == 201
        assert len(resp.json()) == 3

    def test_add_participant_csv(self, authed_client, make_space):
        space = make_space()
        csv_content = "name,email\nAlice,alice@x.com\nBob,bob@x.com\nCharlie,\n"
        resp = authed_client.post(
            f"/spaces/{space['id']}/participants",
            files={"csv_file": ("roster.csv", csv_content, "text/csv")},
        )
        assert resp.status_code == 201
        data = resp.json()
        assert len(data) == 3
        assert data[0]["name"] == "Alice"
        assert data[0]["email"] == "alice@x.com"
        assert data[2]["name"] == "Charlie"
        assert data[2]["email"] is None

    def test_add_participant_csv_missing_name_column(self, authed_client, make_space):
        space = make_space()
        csv_content = "email\nalice@x.com\n"
        resp = authed_client.post(
            f"/spaces/{space['id']}/participants",
            files={"csv_file": ("roster.csv", csv_content, "text/csv")},
        )
        assert resp.status_code == 400

    def test_add_participant_no_body(self, authed_client, make_space):
        space = make_space()
        resp = authed_client.post(f"/spaces/{space['id']}/participants")
        assert resp.status_code == 400

    def test_add_participant_requires_auth(self, client):
        resp = client.post("/spaces/1/participants", json=[{"name": "X"}])
        assert resp.status_code == 401

    def test_add_participant_nonexistent_space(self, authed_client):
        resp = authed_client.post(
            "/spaces/9999/participants", json=[{"name": "X"}]
        )
        assert resp.status_code == 404

    def test_invite_token_is_unique(self, authed_client, make_space):
        space = make_space()
        resp = authed_client.post(
            f"/spaces/{space['id']}/participants",
            json=[{"name": "A"}, {"name": "B"}],
        )
        tokens = [p["invite_token"] for p in resp.json()]
        assert len(tokens) == len(set(tokens))  # all unique


class TestListParticipants:
    def test_list_empty(self, authed_client, make_space):
        space = make_space()
        resp = authed_client.get(f"/spaces/{space['id']}/participants")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_list_ordered_by_position(self, authed_client, make_space):
        space = make_space()
        authed_client.post(
            f"/spaces/{space['id']}/participants",
            json=[{"name": "A"}, {"name": "B"}, {"name": "C"}],
        )
        resp = authed_client.get(f"/spaces/{space['id']}/participants")
        assert resp.status_code == 200
        names = [p["name"] for p in resp.json()]
        assert names == ["A", "B", "C"]

    def test_list_requires_auth(self, client):
        resp = client.get("/spaces/1/participants")
        assert resp.status_code == 401


class TestUpdateParticipant:
    def test_rename_participant(self, authed_client, make_participant):
        space, p = make_participant(name="Old Name")
        resp = authed_client.put(
            f"/spaces/{space['id']}/participants/{p['id']}",
            json={"name": "New Name"},
        )
        assert resp.status_code == 200
        assert resp.json()["name"] == "New Name"

    def test_reorder_participant(self, authed_client, make_space):
        space = make_space()
        resp = authed_client.post(
            f"/spaces/{space['id']}/participants",
            json=[{"name": "A"}, {"name": "B"}, {"name": "C"}],
        )
        participants = resp.json()
        # Move C to position 0
        c_id = participants[2]["id"]
        resp = authed_client.put(
            f"/spaces/{space['id']}/participants/{c_id}",
            json={"position": 0},
        )
        assert resp.status_code == 200
        assert resp.json()["position"] == 0

    def test_update_nonexistent_participant(self, authed_client, make_space):
        space = make_space()
        resp = authed_client.put(
            f"/spaces/{space['id']}/participants/9999",
            json={"name": "X"},
        )
        assert resp.status_code == 404

    def test_update_requires_auth(self, client):
        resp = client.put(
            "/spaces/1/participants/1", json={"name": "X"}
        )
        assert resp.status_code == 401


class TestDeleteParticipant:
    def test_delete_existing(self, authed_client, make_participant):
        space, p = make_participant()
        resp = authed_client.delete(
            f"/spaces/{space['id']}/participants/{p['id']}"
        )
        assert resp.status_code == 204
        # Verify gone from list
        resp = authed_client.get(f"/spaces/{space['id']}/participants")
        assert resp.status_code == 200
        assert len(resp.json()) == 0

    def test_delete_nonexistent(self, authed_client, make_space):
        space = make_space()
        resp = authed_client.delete(
            f"/spaces/{space['id']}/participants/9999"
        )
        assert resp.status_code == 404

    def test_delete_requires_auth(self, client):
        resp = client.delete("/spaces/1/participants/1")
        assert resp.status_code == 401