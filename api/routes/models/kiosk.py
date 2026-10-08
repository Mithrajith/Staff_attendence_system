from typing import Annotated, Literal

from pydantic import BaseModel, StringConstraints

from api.routes.models.attendance import UTCDateTime
from database.models import AttendanceType

# Base64 JPEG/PNG (data URLs accepted).
Frame = Annotated[str, StringConstraints(min_length=100, max_length=2_800_000)]


class FrameIn(BaseModel):
    image: Frame


class RecordIn(BaseModel):
    match_token: Annotated[str, StringConstraints(min_length=20, max_length=2000)]  # from /kiosk/identify
    action: AttendanceType  # the button the person pressed


class DetectOut(BaseModel):
    faces: int  # faces large enough to be someone standing at the screen


class Person(BaseModel):
    name: str | None = None
    employee_id: str | None = None
    department: str | None = None


class IdentifyOut(Person):
    """`matched`: face recognized, nothing recorded yet; `match_token` is needed to record. `no_face`: not exactly
    one usable face in the frame. `unknown`: face not matched to an active account."""

    status: Literal["matched", "no_face", "unknown"]
    match_token: str | None = None
    checked_in: bool | None = None  # current state today, so the UI enables only the valid button
    contact: str | None = None  # name of an admin to contact; set when status is `unknown`


class RecordOut(Person):
    """`recorded`: attendance saved. `already_checked_in` / `not_checked_in`: the action is out of sequence.
    `expired`: the match token is old or invalid, or the account is no longer eligible; nothing saved."""

    status: Literal["recorded", "already_checked_in", "not_checked_in", "expired"]
    action: AttendanceType | None = None
    occurred_at: UTCDateTime | None = None
    hour: int | None = None  # local hour (APP_TIMEZONE) of the event, for time-of-day greetings
