from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from api.deps import Permission, require
from api.services import attendance as svc
from database.models import AttendanceEvent, AttendanceType, Department, User
from database.session import get_db

router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(require(Permission.admin_summary))])

DB = Annotated[Session, Depends(get_db)]


class Summary(BaseModel):
    users_total: int
    users_active: int
    users_face_enrolled: int
    departments: int
    checked_in_today: int  # distinct users with a check-in today
    currently_in: int  # users whose latest event today is a check-in


@router.get("/summary", response_model=Summary)
def summary(db: DB) -> Summary:
    count = lambda stmt: db.scalar(stmt) or 0  # noqa: E731
    today = svc.today()
    latest = (
        select(func.max(AttendanceEvent.id))
        .where(AttendanceEvent.work_date == today)
        .group_by(AttendanceEvent.user_id)
        .subquery()
    )
    return Summary(
        users_total=count(select(func.count()).select_from(User)),
        users_active=count(select(func.count()).select_from(User).where(User.is_active.is_(True))),
        users_face_enrolled=count(select(func.count()).select_from(User).where(User.face_enrolled_at.is_not(None))),
        departments=count(select(func.count()).select_from(Department)),
        checked_in_today=count(
            select(func.count(func.distinct(AttendanceEvent.user_id))).where(
                AttendanceEvent.work_date == today, AttendanceEvent.event_type == AttendanceType.check_in
            )
        ),
        currently_in=count(
            select(func.count())
            .select_from(AttendanceEvent)
            .where(AttendanceEvent.id.in_(select(latest.c[0])), AttendanceEvent.event_type == AttendanceType.check_in)
        ),
    )
