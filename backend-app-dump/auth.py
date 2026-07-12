"""Session-based auth for the single organizer.

- httpOnly cookie named "session" holds an opaque token.
- A SessionRow in the DB maps the token to the organizer.
- No JWT, no refresh rotation, no multi-tenant.
"""
from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone

from fastapi import Depends, HTTPException, Request, status
from passlib.context import CryptContext
from sqlmodel import Session, select

from app.config import settings
from app.db import get_session
from app.models import Organizer, SessionRow


def _utcnow_naive() -> datetime:
    """Naive UTC — SQLite strips tzinfo so we compare naive to naive."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

SESSION_COOKIE = "session"


def hash_password(plain: str) -> str:
    return pwd_context.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)


def create_session(db: Session, organizer_id: int) -> str:
    """Create a session row and return the token to set in the cookie."""
    token = secrets.token_urlsafe(32)
    expires_at = _utcnow_naive() + timedelta(
        seconds=settings.session_max_age_seconds
    )
    row = SessionRow(token=token, organizer_id=organizer_id, expires_at=expires_at)
    db.add(row)
    db.commit()
    db.refresh(row)
    return token


def delete_session(db: Session, token: str) -> None:
    row = db.exec(select(SessionRow).where(SessionRow.token == token)).first()
    if row:
        db.delete(row)
        db.commit()


def get_session_row(db: Session, token: str) -> SessionRow | None:
    row = db.exec(select(SessionRow).where(SessionRow.token == token)).first()
    if row is None:
        return None
    # Expire check (both naive UTC)
    if row.expires_at < _utcnow_naive():
        db.delete(row)
        db.commit()
        return None
    return row


def seed_organizer(db: Session) -> None:
    """Seed the single organizer at startup if not present."""
    existing = db.exec(
        select(Organizer).where(Organizer.username == settings.organizer_username)
    ).first()
    if existing is None:
        org = Organizer(
            username=settings.organizer_username,
            password_hash=hash_password(settings.organizer_password),
        )
        db.add(org)
        db.commit()


def require_organizer(
    request: Request,
    db: Session = Depends(get_session),
) -> Organizer:
    """FastAPI dependency: 401 if no valid organizer session."""
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    row = get_session_row(db, token)
    if row is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired session")
    org = db.get(Organizer, row.organizer_id)
    if org is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Organizer not found")
    return org