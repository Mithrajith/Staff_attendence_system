from datetime import date, datetime, timezone
from typing import Annotated

from pydantic import AfterValidator, BaseModel, ConfigDict, EmailStr

from database.models import AttendanceType


def _as_utc(v: datetime) -> datetime:
    return v.replace(tzinfo=timezone.utc) if v.tzinfo is None else v


# Stored as naive UTC; serialized with an explicit offset so clients convert correctly.
UTCDateTime = Annotated[datetime, AfterValidator(_as_utc)]


class AttendanceToday(BaseModel):
    """Today's state in the app time zone; `work_date` also tells clients what "today" is."""

    work_date: date
    is_checked_in: bool
    first_check_in: UTCDateTime | None
    last_check_out: UTCDateTime | None


class AttendanceStatus(BaseModel):
    """Drives the UI: enable the Check in / Check out buttons from these flags."""

    work_date: date
    face_verified: bool
    is_checked_in: bool
    can_check_in: bool
    can_check_out: bool
    first_check_in: UTCDateTime | None
    last_check_out: UTCDateTime | None


class AttendanceEventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: int
    event_type: AttendanceType
    occurred_at: UTCDateTime
    work_date: date
    face_confidence: float | None


class AttendanceDay(BaseModel):
    """What a user sees: only the day's first check-in and last check-out."""

    user_id: int
    work_date: date
    first_check_in: UTCDateTime | None
    last_check_out: UTCDateTime | None


class AttendanceDayAdmin(AttendanceDay):
    full_name: str
    email: EmailStr
    employee_id: str | None = None
    check_in_count: int
    check_out_count: int


class AttendanceDayList(BaseModel):
    items: list[AttendanceDay]


class AttendanceDayAdminList(BaseModel):
    items: list[AttendanceDayAdmin]
    total: int
    limit: int
    offset: int


class AttendanceEventAdmin(AttendanceEventOut):
    full_name: str | None = None
    employee_id: str | None = None


class AttendanceEventList(BaseModel):
    items: list[AttendanceEventAdmin]
    total: int
    limit: int
    offset: int


class StaffDailyReportItem(BaseModel):
    user_id: int
    full_name: str
    email: EmailStr
    employee_id: str | None = None
    department: str | None = None
    first_check_in: UTCDateTime | None = None
    last_check_out: UTCDateTime | None = None
    total_seconds: int = 0
    total_time_formatted: str = "-"
    is_checked_in: bool = False
    status: str = "absent"  # "checked_in", "checked_out", "absent"


class StaffDailyReport(BaseModel):
    work_date: date
    items: list[StaffDailyReportItem]
    total_staff: int
    present_count: int
    currently_in_count: int
    absent_count: int
