from datetime import date, datetime
from zoneinfo import ZoneInfo

import redis
from fastapi import HTTPException, status
from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from api.core import face_verification
from api.core.config import get_settings
from api.routes.models.attendance import AttendanceStatus
from database.models import AttendanceEvent, AttendanceType, User, utcnow


def today() -> date:
    return datetime.now(ZoneInfo(get_settings().app_timezone)).date()


def _first_in_expr():
    return func.min(case((AttendanceEvent.event_type == AttendanceType.check_in, AttendanceEvent.occurred_at)))


def _last_out_expr():
    return func.max(case((AttendanceEvent.event_type == AttendanceType.check_out, AttendanceEvent.occurred_at)))


def _peek_face(user_id: int) -> float | None:
    try:
        return face_verification.peek(user_id)
    except redis.RedisError:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Face verification service unavailable")


def _consume_face(user_id: int) -> float | None:
    try:
        return face_verification.consume(user_id)
    except redis.RedisError:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Face verification service unavailable")


def _last_event_type(db: Session, user_id: int, work_date: date) -> AttendanceType | None:
    return db.scalar(
        select(AttendanceEvent.event_type)
        .where(AttendanceEvent.user_id == user_id, AttendanceEvent.work_date == work_date)
        .order_by(AttendanceEvent.occurred_at.desc(), AttendanceEvent.id.desc())
        .limit(1)
    )


def get_status(db: Session, user: User) -> AttendanceStatus:
    """Today's state. A button is enabled only if the face is verified AND the action is valid in sequence."""
    day = today()
    last = _last_event_type(db, user.id, day)
    first_in, last_out = db.execute(
        select(_first_in_expr(), _last_out_expr()).where(
            AttendanceEvent.user_id == user.id, AttendanceEvent.work_date == day
        )
    ).one()
    verified = _peek_face(user.id) is not None
    checked_in = last == AttendanceType.check_in
    return AttendanceStatus(
        work_date=day,
        face_verified=verified,
        is_checked_in=checked_in,
        can_check_in=verified and not checked_in,
        can_check_out=verified and checked_in,
        first_check_in=first_in,
        last_check_out=last_out,
    )


def mark(db: Session, user: User, action: AttendanceType) -> AttendanceEvent:
    # Row lock serializes concurrent requests from the same user (no-op on SQLite).
    db.execute(select(User.id).where(User.id == user.id).with_for_update()).one()
    day = today()
    checked_in = _last_event_type(db, user.id, day) == AttendanceType.check_in
    if action == AttendanceType.check_in and checked_in:
        raise HTTPException(status.HTTP_409_CONFLICT, "Already checked in")
    if action == AttendanceType.check_out and not checked_in:
        raise HTTPException(status.HTTP_409_CONFLICT, "You must check in before checking out")

    confidence = _consume_face(user.id)
    if confidence is None:
        db.rollback()
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Face not verified. Look at the camera and try again")

    event = AttendanceEvent(
        user_id=user.id, event_type=action, occurred_at=utcnow(), work_date=day, face_confidence=confidence
    )
    db.add(event)
    db.commit()
    return event


def is_checked_in(db: Session, user: User) -> bool:
    return _last_event_type(db, user.id, today()) == AttendanceType.check_in


def record_scan(db: Session, user: User, action: AttendanceType, face_confidence: float) -> AttendanceEvent | None:
    """Kiosk flow: the face was recognized just now, so no Redis flag is needed.

    Returns None (nothing saved) if the action is out of sequence: in while already in, or out while not in.
    """
    db.execute(select(User.id).where(User.id == user.id).with_for_update()).one()
    day = today()
    checked_in = _last_event_type(db, user.id, day) == AttendanceType.check_in
    if checked_in == (action == AttendanceType.check_in):
        db.rollback()
        return None
    event = AttendanceEvent(
        user_id=user.id, event_type=action, occurred_at=utcnow(), work_date=day, face_confidence=face_confidence
    )
    db.add(event)
    db.commit()
    return event


def daily_summaries(
    db: Session, *, date_from: date, date_to: date, user_id: int | None = None, limit: int | None = None, offset: int = 0
):
    """Per (user, day) first check-in / last check-out plus counts. Returns (rows, total)."""
    stmt = (
        select(
            AttendanceEvent.user_id,
            AttendanceEvent.work_date,
            _first_in_expr().label("first_check_in"),
            _last_out_expr().label("last_check_out"),
            func.sum(case((AttendanceEvent.event_type == AttendanceType.check_in, 1), else_=0)).label("check_in_count"),
            func.sum(case((AttendanceEvent.event_type == AttendanceType.check_out, 1), else_=0)).label("check_out_count"),
        )
        .where(AttendanceEvent.work_date >= date_from, AttendanceEvent.work_date <= date_to)
        .group_by(AttendanceEvent.user_id, AttendanceEvent.work_date)
    )
    if user_id is not None:
        stmt = stmt.where(AttendanceEvent.user_id == user_id)
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    stmt = stmt.order_by(AttendanceEvent.work_date.desc(), AttendanceEvent.user_id)
    if limit is not None:
        stmt = stmt.limit(limit).offset(offset)
    return db.execute(stmt).all(), total


def list_events(
    db: Session, *, date_from: date, date_to: date, user_id: int | None, limit: int, offset: int
):
    stmt = select(AttendanceEvent).where(
        AttendanceEvent.work_date >= date_from, AttendanceEvent.work_date <= date_to
    )
    if user_id is not None:
        stmt = stmt.where(AttendanceEvent.user_id == user_id)
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    items = db.scalars(
        stmt.order_by(AttendanceEvent.occurred_at.desc(), AttendanceEvent.id.desc()).limit(limit).offset(offset)
    ).all()
    return items, total
