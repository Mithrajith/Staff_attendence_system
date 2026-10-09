from datetime import date, datetime, timedelta
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
    AttendanceEventAdmin,
    AttendanceEventList,
    AttendanceEventOut,
    AttendanceStatus,
    AttendanceToday,
    StaffDailyReport,
    StaffDailyReportItem,
)
from api.services import attendance as svc
from database.models import AttendanceEvent, AttendanceType, Role, User, utcnow
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


@router.get("/me/today", response_model=AttendanceToday)
def my_today(user: CurrentUser, db: DB) -> AttendanceToday:
    """The caller's own state for today (first in, last out, currently in?). Used by the staff portal."""
    return svc.get_today(db, user)


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
            employee_id=users[r.user_id].employee_id,
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
    users = {u.id: u for u in db.scalars(select(User).where(User.id.in_({e.user_id for e in items})))}
    out = []
    for e in items:
        row = AttendanceEventAdmin.model_validate(e)
        u = users.get(e.user_id)
        if u:
            row.full_name, row.employee_id = u.full_name, u.employee_id
        out.append(row)
    return AttendanceEventList(items=out, total=total, limit=limit, offset=offset)


@router.get(
    "/daily-report",
    response_model=StaffDailyReport,
    dependencies=[Depends(require(Permission.attendance_read_any))],
)
def daily_staff_report(
    db: DB,
    work_date: date | None = None,
) -> StaffDailyReport:
    """Admin report: lists every staff member with today's (or given date's) check-in, check-out, and total time."""
    day = work_date or svc.today()
    now_utc = utcnow()
    is_today = day == svc.today()

    staff_users = db.scalars(
        select(User)
        .where(User.role != Role.system, User.is_active.is_(True))
        .order_by(User.full_name)
    ).all()

    events = db.scalars(
        select(AttendanceEvent)
        .where(AttendanceEvent.work_date == day)
        .order_by(AttendanceEvent.user_id, AttendanceEvent.occurred_at)
    ).all()

    user_events: dict[int, list[AttendanceEvent]] = {}
    for ev in events:
        user_events.setdefault(ev.user_id, []).append(ev)

    items: list[StaffDailyReportItem] = []
    present_count = 0
    currently_in_count = 0

    for u in staff_users:
        u_events = user_events.get(u.id, [])
        first_in: datetime | None = None
        last_out: datetime | None = None

        for ev in u_events:
            if ev.event_type == AttendanceType.check_in and first_in is None:
                first_in = ev.occurred_at
            elif ev.event_type == AttendanceType.check_out:
                last_out = ev.occurred_at

        total_sec = 0
        open_in: datetime | None = None
        for ev in u_events:
            if ev.event_type == AttendanceType.check_in:
                if open_in is None:
                    open_in = ev.occurred_at
            elif ev.event_type == AttendanceType.check_out:
                if open_in is not None:
                    diff = (ev.occurred_at - open_in).total_seconds()
                    if diff > 0:
                        total_sec += int(diff)
                    open_in = None

        is_in = False
        if open_in is not None:
            is_in = True
            if is_today:
                diff = (now_utc - open_in).total_seconds()
                if diff > 0:
                    total_sec += int(diff)

        if not u_events:
            status_str = "absent"
            formatted_time = "-"
        elif is_in:
            status_str = "checked_in"
            present_count += 1
            currently_in_count += 1
            hrs = total_sec // 3600
            mins = (total_sec % 3600) // 60
            formatted_time = f"{hrs}h {mins}m"
        else:
            status_str = "checked_out"
            present_count += 1
            hrs = total_sec // 3600
            mins = (total_sec % 3600) // 60
            formatted_time = f"{hrs}h {mins}m"

        items.append(
            StaffDailyReportItem(
                user_id=u.id,
                full_name=u.full_name,
                email=u.email,
                employee_id=u.employee_id,
                department=u.department.name if u.department else None,
                first_check_in=first_in,
                last_check_out=last_out,
                total_seconds=total_sec,
                total_time_formatted=formatted_time,
                is_checked_in=is_in,
                status=status_str,
            )
        )

    absent_count = len(staff_users) - present_count
    return StaffDailyReport(
        work_date=day,
        items=items,
        total_staff=len(staff_users),
        present_count=present_count,
        currently_in_count=currently_in_count,
        absent_count=absent_count,
    )
