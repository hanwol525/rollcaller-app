"""Auth routes: login, logout, me."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel
from sqlmodel import Session, select

from app.auth import (
    SESSION_COOKIE,
    create_session,
    delete_session,
    require_organizer,
    verify_password,
)
from app.config import settings
from app.db import get_session
from app.models import Organizer, OrganizerRead

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginRequest(BaseModel):
    username: str
    password: str


@router.post("/login")
def login(body: LoginRequest, response: Response, db: Session = Depends(get_session)):
    row = db.exec(
        select(Organizer).where(Organizer.username == body.username)
    ).first()
    if row is None or not verify_password(body.password, row.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password",
        )
    token = create_session(db, row.id)
    response.set_cookie(
        key=SESSION_COOKIE,
        value=token,
        httponly=True,
        samesite="lax",
        secure=False,  # dev; set True behind HTTPS in prod
        max_age=settings.session_max_age_seconds,
        path="/",
    )
    return {"ok": True}


@router.post("/logout")
def logout(request: Request, response: Response, db: Session = Depends(get_session)):
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        delete_session(db, token)
    response.delete_cookie(key=SESSION_COOKIE, path="/")
    return {"ok": True}


@router.get("/me", response_model=OrganizerRead)
def me(org: Organizer = Depends(require_organizer)):
    return org