"""Tests for /spaces endpoints: create, list, get, update, delete."""
from __future__ import annotations


class TestCreateSpace:
    def test_create_space_success(self, authed_client):
        resp = authed_client.post(
            "/spaces", json={"name": "Graduation 2026", "advanced_seconds": 10}
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["name"] == "Graduation 2026"
        assert data["advanced_seconds"] == 10
        assert "id" in data
        assert "created_at" in data

    def test_create_space_default_advanced_seconds(self, authed_client):
        resp = authed_client.post("/spaces", json={"name": "Default"})
        assert resp.status_code == 201
        assert resp.json()["advanced_seconds"] == 5

    def test_create_space_requires_auth(self, client):
        resp = client.post("/spaces", json={"name": "No Auth"})
        assert resp.status_code == 401

    def test_create_space_missing_name(self, authed_client):
        resp = authed_client.post("/spaces", json={"advanced_seconds": 5})
        assert resp.status_code == 422


class TestListSpaces:
    def test_list_empty(self, authed_client):
        resp = authed_client.get("/spaces")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_list_multiple(self, authed_client):
        for i in range(3):
            authed_client.post("/spaces", json={"name": f"Space {i}"})
        resp = authed_client.get("/spaces")
        assert resp.status_code == 200
        assert len(resp.json()) == 3

    def test_list_requires_auth(self, client):
        resp = client.get("/spaces")
        assert resp.status_code == 401


class TestGetSpace:
    def test_get_existing(self, authed_client, make_space):
        space = make_space(name="My Space")
        resp = authed_client.get(f"/spaces/{space['id']}")
        assert resp.status_code == 200
        assert resp.json()["name"] == "My Space"

    def test_get_nonexistent(self, authed_client):
        resp = authed_client.get("/spaces/9999")
        assert resp.status_code == 404

    def test_get_requires_auth(self, client):
        resp = client.get("/spaces/1")
        assert resp.status_code == 401


class TestUpdateSpace:
    def test_rename_space(self, authed_client, make_space):
        space = make_space(name="Old Name")
        resp = authed_client.put(
            f"/spaces/{space['id']}", json={"name": "New Name"}
        )
        assert resp.status_code == 200
        assert resp.json()["name"] == "New Name"

    def test_update_advanced_seconds(self, authed_client, make_space):
        space = make_space(advanced_seconds=5)
        resp = authed_client.put(
            f"/spaces/{space['id']}", json={"advanced_seconds": 15}
        )
        assert resp.status_code == 200
        assert resp.json()["advanced_seconds"] == 15

    def test_update_nonexistent(self, authed_client):
        resp = authed_client.put("/spaces/9999", json={"name": "X"})
        assert resp.status_code == 404

    def test_update_requires_auth(self, client):
        resp = client.put("/spaces/1", json={"name": "X"})
        assert resp.status_code == 401


class TestDeleteSpace:
    def test_delete_existing(self, authed_client, make_space):
        space = make_space()
        resp = authed_client.delete(f"/spaces/{space['id']}")
        assert resp.status_code == 204
        # Verify gone
        resp = authed_client.get(f"/spaces/{space['id']}")
        assert resp.status_code == 404

    def test_delete_nonexistent(self, authed_client):
        resp = authed_client.delete("/spaces/9999")
        assert resp.status_code == 404

    def test_delete_requires_auth(self, client):
        resp = client.delete("/spaces/1")
        assert resp.status_code == 401

    def test_delete_cascades_to_participants(self, authed_client, make_space):
        """Deleting a space must also delete all its participants."""
        space = make_space()
        # Add a participant
        authed_client.post(
            f"/spaces/{space['id']}/participants",
            json=[{"name": "Alice", "email": "a@x.com"}],
        )
        # Delete the space
        resp = authed_client.delete(f"/spaces/{space['id']}")
        assert resp.status_code == 204
        # Participants should be gone
        resp = authed_client.get(f"/spaces/{space['id']}/participants")
        assert resp.status_code == 404