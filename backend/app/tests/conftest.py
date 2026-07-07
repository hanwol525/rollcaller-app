"""Shared pytest fixtures for the rollcaller test suite.

Uses SQLite in-memory + a temp filesystem blobstore so tests run with zero
external dependencies (no Postgres, no MinIO, no espeak-ng, no Allosaurus).
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# 1. Set env vars BEFORE importing the app so config/settings pick them up.
# ---------------------------------------------------------------------------
os.environ["DATABASE_URL"] = "sqlite://"
os.environ["STORAGE_BACKEND"] = "filesystem"
_tmp_blob = tempfile.mkdtemp(prefix="rollcaller_test_blob_")
os.environ["STORAGE_FS_ROOT"] = _tmp_blob
os.environ["ORGANIZER_USERNAME"] = "organizer"
os.environ["ORGANIZER_PASSWORD"] = "testpass123"

# Make `app` importable when running from the backend/ directory
# (so `from app.main import app` works in tests).
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

# Now it's safe to import app modules
from fastapi.testclient import TestClient  # noqa: E402
from sqlmodel import Session, SQLModel, create_engine  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from app.config import settings  # noqa: E402
from app.db import get_session  # noqa: E402
from app.main import app  # noqa: E402
from app.auth import hash_password, SESSION_COOKIE  # noqa: E402
from app.models import Organizer, Space, Participant  # noqa: E402


# ---------------------------------------------------------------------------
# 2. Override the DB engine/session for an isolated in-memory SQLite per test.
# ---------------------------------------------------------------------------
@pytest.fixture()
def db_engine():
    """Fresh in-memory SQLite engine for each test."""
    eng = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(eng)
    yield eng
    SQLModel.metadata.drop_all(eng)
    eng.dispose()


@pytest.fixture()
def db_session(db_engine):
    """Yield a SQLModel Session bound to the test engine."""
    with Session(db_engine) as session:
        yield session


@pytest.fixture()
def client(db_engine):
    """TestClient with the DB dependency overridden to use the test engine."""
    def _get_test_session():
        with Session(db_engine) as s:
            yield s

    app.dependency_overrides[get_session] = _get_test_session

    # Seed organizer for every test (lifespan also does this but with the
    # default engine; we re-seed into the overridden test engine).
    with Session(db_engine) as s:
        from app.auth import seed_organizer
        seed_organizer(s)

    with TestClient(app) as c:
        # Reset pronunciation module state AFTER lifespan startup so tests
        # use fallbacks, not the real ML models that warm_all() loaded.
        from app.pronunciation import g2p, recognize, tts
        g2p._backend = None
        recognize._recognizer = None
        tts._tts = None
        yield c

    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# 3. Auth helper: returns an authenticated client (session cookie set).
# ---------------------------------------------------------------------------
@pytest.fixture()
def authed_client(client):
    resp = client.post(
        "/auth/login",
        json={"username": "organizer", "password": "testpass123"},
    )
    assert resp.status_code == 200, resp.text
    assert SESSION_COOKIE in resp.cookies
    yield client


# ---------------------------------------------------------------------------
# 4. Helper builders
# ---------------------------------------------------------------------------
@pytest.fixture()
def make_space(authed_client):
    """Create a space and return the created SpaceRead dict."""
    def _make(name: str = "Test Space", advanced_seconds: int = 5):
        resp = authed_client.post(
            "/spaces", json={"name": name, "advanced_seconds": advanced_seconds}
        )
        assert resp.status_code == 201, resp.text
        return resp.json()

    return _make


@pytest.fixture()
def make_participant(authed_client, make_space):
    """Create a space + one participant; return (space, participant)."""
    def _make(name: str = "Alice Lee", email: str | None = "alice@example.com"):
        space = make_space()
        resp = authed_client.post(
            f"/spaces/{space['id']}/participants",
            json=[{"name": name, "email": email}],
        )
        assert resp.status_code == 201, resp.text
        participants = resp.json()
        return space, participants[0]

    return _make


@pytest.fixture()
def wav_bytes():
    """Return a minimal valid WAV file as bytes (for upload tests)."""
    import io
    import wave
    import struct
    import math

    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        for i in range(1600):  # 0.1 seconds
            v = int(16000 * math.sin(2 * math.pi * 440 * (i / 16000)))
            w.writeframes(struct.pack("<h", v))
    return buf.getvalue()


# ---------------------------------------------------------------------------
# 5. Cleanup
# ---------------------------------------------------------------------------
@pytest.fixture(autouse=True)
def _clean_blobstore():
    yield
    # Clear the temp blob directory after each test
    if os.path.isdir(_tmp_blob):
        shutil.rmtree(_tmp_blob, ignore_errors=True)
        os.makedirs(_tmp_blob, exist_ok=True)