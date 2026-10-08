from datetime import date, datetime, timezone
from enum import Enum

from sqlalchemy import Boolean, Date, DateTime, Enum as SAEnum, Float, ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from database.session import Base


def utcnow() -> datetime:
    """Naive UTC, matching MySQL DATETIME columns."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Role(str, Enum):
    admin = "admin"
    system = "system"
    staff = "staff"


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    email: Mapped[str] = mapped_column(String(254), unique=True, index=True)
    full_name: Mapped[str] = mapped_column(String(120))
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[Role] = mapped_column(
        SAEnum(Role, native_enum=False, length=16, values_callable=lambda e: [m.value for m in e]),
        index=True,
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    # Bumped on every password change; tokens carrying an older value are rejected.
    token_version: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class PasswordResetToken(Base):
    __tablename__ = "password_reset_tokens"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)  # sha256 hex; raw token is never stored
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class AttendanceType(str, Enum):
    check_in = "check_in"
    check_out = "check_out"


class AttendanceEvent(Base):
    """Append-only log of check-ins/check-outs. Daily first-in/last-out is derived from it."""

    __tablename__ = "attendance_events"
    __table_args__ = (Index("ix_attendance_events_user_work_date", "user_id", "work_date"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    event_type: Mapped[AttendanceType] = mapped_column(
        SAEnum(AttendanceType, native_enum=False, length=16, values_callable=lambda e: [m.value for m in e])
    )
    occurred_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)  # UTC
    work_date: Mapped[date] = mapped_column(Date, index=True)  # local date in APP_TIMEZONE
    face_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
