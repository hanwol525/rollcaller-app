"""FastAPI app entry point.

- Warms the four pronunciation stages once at startup via lifespan: eSpeak
  g2p, the wav2vec2 recognizer, Kokoro TTS and the ffmpeg path.
- Seeds the single organizer at startup.
- Registers all routers.
- Serves /media/{key} in dev (filesystem); MinIO serves directly in prod.
"""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, status
from fastapi.responses import Response
from sqlmodel import Session

from app.auth import seed_organizer
from app.db import engine, init_db
from app.pronunciation import orchestrator
from app.routers import auth as auth_router
from app.routers import ceremony as ceremony_router
from app.routers import invite as invite_router
from app.routers import participants as participants_router
from app.routers import spaces as spaces_router
from app.storage import storage


@asynccontextmanager
async def lifespan(app: FastAPI):
    # --- Startup ---
    # 1. Create tables
    init_db()
    # 2. Seed the single organizer
    with Session(engine) as db:
        seed_organizer(db)
    # 3. Warm the pronunciation stages once. Every stage degrades on failure
    #    and now logs why, so a missing model never blocks startup. Called
    #    through the module attribute so tests can neuter it.
    orchestrator.warm_all()
    yield
    # --- Shutdown ---
    # (nothing to clean up synchronously)


app = FastAPI(title="RollCaller", version="0.1.0", lifespan=lifespan)

# Register routers
app.include_router(auth_router.router)
app.include_router(spaces_router.router)
app.include_router(participants_router.router)
app.include_router(ceremony_router.router)
app.include_router(invite_router.router)


@app.get("/health", tags=["health"])
def health():
    return {"ok": True}


@app.get("/media/{key}", tags=["media"])
def serve_media(key: str):
    """Serve an audio file from storage (any backend).

    In dev (filesystem) this reads from disk. In prod (MinIO) this proxies
    from the MinIO sidecar. The URL returned by storage.url() is always
    /media/{key} so the browser never needs to reach MinIO directly.
    """
    try:
        data = storage.load(key)
    except FileNotFoundError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File not found")
    except Exception:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Storage error")
    return Response(content=data, media_type="audio/wav")