"""FastAPI app entry point.

- Loads Kokoro + Allosaurus once at startup via lifespan (warm instances).
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
from app.pronunciation.orchestrator import warm_all
from app.routers import auth as auth_router
from app.routers import ceremony as ceremony_router
from app.routers import invite as invite_router
from app.routers import participants as participants_router
from app.routers import spaces as spaces_router
from app.storage import storage
from app.config import settings


@asynccontextmanager
async def lifespan(app: FastAPI):
    # --- Startup ---
    # 1. Create tables
    init_db()
    # 2. Seed the single organizer
    with Session(engine) as db:
        seed_organizer(db)
    # 3. Warm heavy ML models (Kokoro, Allosaurus) once
    warm_all()
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
    """Serve an audio file from the filesystem in dev.

    In prod (MinIO backend), the client is given a presigned URL directly and
    doesn't hit this endpoint.
    """
    if settings.storage_backend != "filesystem":
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Media endpoint not available in prod; use presigned URLs.",
        )
    try:
        data = storage.load(key)
    except FileNotFoundError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File not found")
    return Response(content=data, media_type="audio/wav")