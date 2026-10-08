from datetime import date, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from api.deps import CurrentUser, Permission, require
from api.routes.models.attendance import (
    AttendanceDay,
    AttendanceDayAdmin,
    AttendanceDayAdminList,
    AttendanceDayList,
    AttendanceEventList,
    AttendanceEventOut,
    AttendanceStatus,
)
from api.services import attendance as svc
from database.models import AttendanceEvent, AttendanceType, User
from database.session import get_db

router = APIRouter(prefix="/attendance", tags=["attendance"])

DB = Annotated[Session, Depends(get_db)]
MAX_RANGE_DAYS = 366


class DateRange:
    """`date_from`/`date_to` query params. Defaults to the 1st of the current month through today."""

    def __init__(self, date_from: date | None = None, date_to: date | None = None):
        end = date_to or svc.today()
        start = date_from or end.replace(day=1)
        if start > end:
            raise HTTPException(422, "date_from must not be after date_to")
        if (end - start) > timedelta(days=MAX_RANGE_DAYS):
            raise HTTPException(422, f"Range cannot exceed {MAX_RANGE_DAYS} days")
        self.start, self.end = start, end


Range = Annotated[DateRange, Depends()]
Limit = Annotated[int, Query(ge=1, le=200)]
Offset = Annotated[int, Query(ge=0)]


@router.get("/me/status", response_model=AttendanceStatus)
def my_status(user: CurrentUser, db: DB) -> AttendanceStatus:
    """Poll this to enable/disable the Check in / Check out buttons."""
    return svc.get_status(db, user)


@router.post(
    "/check-in",
    response_model=AttendanceEventOut,
    status_code=status.HTTP_201_CREATED,
)
def check_in(user: Annotated[User, Depends(require(Permission.attendance_mark_self))], db: DB) -> AttendanceEvent:
    return svc.mark(db, user, AttendanceType.check_in)


@router.post(
    "/check-out",
    response_model=AttendanceEventOut,
    status_code=status.HTTP_201_CREATED,
)
def check_out(user: Annotated[User, Depends(require(Permission.attendance_mark_self))], db: DB) -> AttendanceEvent:
    return svc.mark(db, user, AttendanceType.check_out)


@router.get("/me", response_model=AttendanceDayList)
def my_attendance(user: CurrentUser, db: DB, rng: Range) -> AttendanceDayList:
    """The caller's own days: first check-in and last check-out only."""
    rows, _ = svc.daily_summaries(db, date_from=rng.start, date_to=rng.end, user_id=user.id)
    return AttendanceDayList(items=[AttendanceDay.model_validate(r, from_attributes=True) for r in rows])


@router.get(
    "/records", response_model=AttendanceDayAdminList, dependencies=[Depends(require(Permission.attendance_read_any))]
)
def all_records(
    db: DB, rng: Range, user_id: int | None = None, limit: Limit = 50, offset: Offset = 0
) -> AttendanceDayAdminList:
    """Admin: per-user daily summaries (first in, last out, counts) across everyone."""
    rows, total = svc.daily_summaries(
        db, date_from=rng.start, date_to=rng.end, user_id=user_id, limit=limit, offset=offset
    )
    users = {u.id: u for u in db.scalars(select(User).where(User.id.in_({r.user_id for r in rows})))}
    items = [
        AttendanceDayAdmin(
            user_id=r.user_id,
            work_date=r.work_date,
            first_check_in=r.first_check_in,
            last_check_out=r.last_check_out,
            check_in_count=r.check_in_count,
            check_out_count=r.check_out_count,
            full_name=users[r.user_id].full_name,
            email=users[r.user_id].email,
        )
        for r in rows
    ]
    return AttendanceDayAdminList(items=items, total=total, limit=limit, offset=offset)


@router.get(
    "/events", response_model=AttendanceEventList, dependencies=[Depends(require(Permission.attendance_read_any))]
)
def all_events(
    db: DB, rng: Range, user_id: int | None = None, limit: Limit = 50, offset: Offset = 0
) -> AttendanceEventList:
    """Admin: every raw check-in / check-out event, newest first."""
    items, total = svc.list_events(
        db, date_from=rng.start, date_to=rng.end, user_id=user_id, limit=limit, offset=offset
    )
    return AttendanceEventList(items=items, total=total, limit=limit, offset=offset)
