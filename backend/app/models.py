"""SQLModel tables and Pydantic schemas for the rollcaller app.

Key invariant enforced on Participant:
  - ipa_text and ipa_source move together: either both None or both filled.
  - If ipa_source in {"g2p", "recognized"}, ipa_confirmed must be True.
  - Only ipa_source == "manual" can coexist with ipa_confirmed == False
    (a human edit that hasn't been confirmed yet keeps the prior source).
  - None/None = "no pronunciation received".
"""
from __future__ import annotations

import secrets
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from sqlmodel import Field, SQLModel


def _utcnow() -> datetime:
    """Naive UTC datetime — SQLite strips tzinfo, so we store naive consistently."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


# --------------------------------------------------------------------------- #
# Enums
# --------------------------------------------------------------------------- #
class ParticipantStatus(str, Enum):
    invited = "invited"
    recorded = "recorded"
    confirmed = "confirmed"


class IPASource(str, Enum):
    g2p = "g2p"              # grapheme-to-phoneme (Espeak NG / phonemizer)
    recognized = "recognized"  # audio-to-IPA (Allosaurus)
    manual = "manual"        # human-edited IPA


# --------------------------------------------------------------------------- #
# Tables
# --------------------------------------------------------------------------- #
class Organizer(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    username: str = Field(unique=True, index=True)
    # Store a bcrypt hash, never the plaintext.
    password_hash: str
    created_at: datetime = Field(default_factory=_utcnow)


class SessionRow(SQLModel, table=True):
    """Server-side session row keyed by an opaque token stored in the cookie."""
    id: Optional[int] = Field(default=None, primary_key=True)
    token: str = Field(
        default_factory=lambda: secrets.token_urlsafe(32),
        unique=True,
        index=True,
    )
    organizer_id: int = Field(foreign_key="organizer.id", index=True)
    created_at: datetime = Field(default_factory=_utcnow)
    expires_at: datetime


class Space(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str
    advanced_seconds: int = 5  # seconds between name reads during ceremony
    created_at: datetime = Field(default_factory=_utcnow)


class Participant(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    space_id: int = Field(foreign_key="space.id", index=True)
    name: str
    email: Optional[str] = None
    invite_token: str = Field(
        default_factory=lambda: secrets.token_urlsafe(32),
        unique=True,
        index=True,
    )
    status: ParticipantStatus = Field(default=ParticipantStatus.invited)
    position: int = 0  # walking order; NOT based on last-name alphabetical order
    ipa_text: Optional[str] = None
    ipa_source: Optional[str] = None  # "g2p" | "recognized" | "manual"
    ipa_confirmed: bool = False
    recording_key: Optional[str] = None
    clip_key: Optional[str] = None
    created_at: datetime = Field(default_factory=_utcnow)

    # ----- Explicit invariant check (call before db.commit) ----------------- #
    # SQLModel table=True models do NOT run @model_validator on attribute
    # assignment. Routes must call validate_ipa_invariant() before committing
    # to enforce the ipa_text/ipa_source coupling rules.
    def validate_ipa_invariant(self) -> "Participant":
        """Raise ValueError if the ipa_text/ipa_source invariant is violated."""
        ipa_text = self.ipa_text
        ipa_source = self.ipa_source

        # Rule 1: both None or both filled — never one without the other.
        if (ipa_text is None) != (ipa_source is None):
            raise ValueError(
                "ipa_text and ipa_source must move together: "
                "either both None or both filled."
            )

        # Rule 2: if source is g2p or recognized, ipa_confirmed must be True.
        if ipa_source in (IPASource.g2p.value, IPASource.recognized.value):
            if not self.ipa_confirmed:
                raise ValueError(
                    f"ipa_source '{ipa_source}' requires ipa_confirmed=True."
                )

        # Rule 3: manual is the only source allowed when ipa_confirmed is False.
        if ipa_text is not None and not self.ipa_confirmed:
            if ipa_source != IPASource.manual.value:
                raise ValueError(
                    "Unconfirmed IPA must have ipa_source='manual' "
                    "(keeps prior source until confirmed)."
                )

        return self


# --------------------------------------------------------------------------- #
# Read / write schemas (Pydantic models, not tables)
# --------------------------------------------------------------------------- #
class OrganizerRead(SQLModel):
    id: int
    username: str


class SpaceCreate(SQLModel):
    name: str
    advanced_seconds: int = 5


class SpaceUpdate(SQLModel):
    name: Optional[str] = None
    advanced_seconds: Optional[int] = None


class SpaceRead(SQLModel):
    id: int
    name: str
    advanced_seconds: int
    created_at: datetime


class ParticipantCreate(SQLModel):
    name: str
    email: Optional[str] = None


class ParticipantUpdate(SQLModel):
    name: Optional[str] = None
    email: Optional[str] = None
    position: Optional[int] = None


class ParticipantRead(SQLModel):
    """What the organizer dashboard sees."""
    id: int
    name: str
    email: Optional[str]
    status: ParticipantStatus
    position: int
    ipa_text: Optional[str]
    ipa_source: Optional[str]
    ipa_confirmed: bool
    invite_token: str
    recording_key: Optional[str]
    clip_key: Optional[str]


class ParticipantSelf(SQLModel):
    """What the participant sees via invite link — only their own info."""
    id: int
    name: str
    space_name: str          # name of the space/event, for participant screens
    status: ParticipantStatus
    ipa_text: Optional[str]
    ipa_confirmed: bool


class IPAPreviewRequest(SQLModel):
    ipa: str


class IPAConfirmRequest(SQLModel):
    ipa: str
    # Whether this is a human edit. If True and ipa differs from current,
    # ipa_source is set to "manual". If False/None, source follows pipeline.
    is_edit: bool = False


class CeremonyItem(SQLModel):
    """One row in the ceremony playback list."""
    position: int
    name: str
    clip_url: Optional[str]
    ipa_text: Optional[str]
    ipa_source: Optional[str]


class CeremonyData(SQLModel):
    space_id: int
    space_name: str
    advanced_seconds: int
    roster: list[CeremonyItem]