import secrets
from datetime import datetime, timezone
from enum import Enum
from sqlmodel import SQLModel, Field

class ParticipantStatus(str, Enum):
  invited = "invited"
  recorded = "recorded"
  confirmed = "confirmed"

# table
class Participant(SQLModel, table=True):
  id: int | None = Field(default=None, primary_key=True)
  space_id: int = Field(foreign_key="space.id", index=True)
  name: str
  email: str | None = None
  invite_token: str = Field(
    default_factory=lambda: secrets.token_urlsafe(32),
    unique=True, index=True
  )
  status: ParticipantStatus = ParticipantStatus.invited
  position: int = 0 # walking order; NOT based on last-name alphabetical order
  ipa_text: str | None = None
  ipa_source: str | None = None # g2p (grapheme to phoneme), confirmed-by-human, etc.
  ipa_confirmed: bool = False
  recording_key: str | None = None
  clip_key: str | None = None
  created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

# what the reader acc sees
class ParticipantRead(SQLModel):
  id: int; name: str; email: str | None
  status: ParticipantStatus
  invite_token: str # it's for the url
  ipa_text: str | None; ipa_text: str | None