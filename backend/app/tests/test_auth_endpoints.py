"""Tests for /auth endpoints: login, logout, me."""
from __future__ import annotations

from app.auth import SESSION_COOKIE


class TestLogin:
    def test_login_success(self, client):
        resp = client.post(
            "/auth/login",
            json={"username": "organizer", "password": "testpass123"},
        )
        assert resp.status_code == 200
        assert resp.json() == {"ok": True}
        assert SESSION_COOKIE in resp.cookies

    def test_login_wrong_password(self, client):
        resp = client.post(
            "/auth/login",
            json={"username": "organizer", "password": "wrong"},
        )
        assert resp.status_code == 401

    def test_login_unknown_user(self, client):
        resp = client.post(
            "/auth/login",
            json={"username": "nobody", "password": "x"},
        )
        assert resp.status_code == 401

    def test_login_missing_fields(self, client):
        resp = client.post("/auth/login", json={"username": "organizer"})
        assert resp.status_code == 422


class TestLogout:
    def test_logout_clears_cookie(self, authed_client):
        # Verify logged in
        resp = authed_client.get("/auth/me")
        assert resp.status_code == 200

        # Logout
        resp = authed_client.post("/auth/logout")
        assert resp.status_code == 200
        assert resp.json() == {"ok": True}

        # Subsequent /me should 401 (cookie cleared client-side by TestClient)
        resp = authed_client.get("/auth/me")
        assert resp.status_code == 401

    def test_logout_without_session(self, client):
        # Logout without being logged in — should still succeed (idempotent)
        resp = client.post("/auth/logout")
        assert resp.status_code == 200


class TestMe:
    def test_me_with_session(self, authed_client):
        resp = authed_client.get("/auth/me")
        assert resp.status_code == 200
        data = resp.json()
        assert data["username"] == "organizer"
        assert "id" in data
        assert "password_hash" not in data

    def test_me_without_session(self, client):
        resp = client.get("/auth/me")
        assert resp.status_code == 401

    def test_me_with_invalid_cookie(self, client):
        resp = client.get(
            "/auth/me",
            cookies={SESSION_COOKIE: "invalid-token-value"},
        )
        assert resp.status_code == 401