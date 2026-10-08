from datetime import timedelta

from fastapi import BackgroundTasks
from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from api.core.config import get_settings
from api.core.mailer import send_email
from api.core.security import hash_password, hash_reset_token, new_reset_token
from database.models import PasswordResetToken, User, utcnow


def _issue_reset_token(db: Session, user: User) -> str:
    """Invalidates outstanding tokens for the user and stores a fresh one. Caller commits."""
    db.execute(delete(PasswordResetToken).where(PasswordResetToken.user_id == user.id))
    raw, hashed = new_reset_token()
    expires = utcnow() + timedelta(minutes=get_settings().password_reset_expire_minutes)
    db.add(PasswordResetToken(user_id=user.id, token_hash=hashed, expires_at=expires))
    return raw


def _queue_token_email(bg: BackgroundTasks, user: User, raw_token: str, template: str, subject: str) -> None:
    s = get_settings()
    bg.add_task(
        send_email,
        user.email,
        subject,
        template,
        full_name=user.full_name,
        role=user.role.value,
        link=f"{s.frontend_url.rstrip('/')}/reset-password?token={raw_token}",
        expires_minutes=s.password_reset_expire_minutes,
    )


def send_welcome(db: Session, bg: BackgroundTasks, user: User) -> None:
    raw = _issue_reset_token(db, user)
    db.commit()
    _queue_token_email(bg, user, raw, "welcome", "Set up your Staff Attendance account")


def send_password_reset(db: Session, bg: BackgroundTasks, user: User) -> None:
    raw = _issue_reset_token(db, user)
    db.commit()
    _queue_token_email(bg, user, raw, "reset_password", "Reset your Staff Attendance password")


def set_password(db: Session, user: User, new_password: str) -> None:
    """Sets the password, revokes existing sessions and reset tokens. Caller commits."""
    user.password_hash = hash_password(new_password)
    user.token_version += 1
    db.execute(delete(PasswordResetToken).where(PasswordResetToken.user_id == user.id))


def consume_reset_token(db: Session, raw_token: str) -> User | None:
    """Atomically marks the token used; returns its user if valid (unused, unexpired, active)."""
    hashed = hash_reset_token(raw_token)
    now = utcnow()
    claimed = db.execute(
        update(PasswordResetToken)
        .where(
            PasswordResetToken.token_hash == hashed,
            PasswordResetToken.used_at.is_(None),
            PasswordResetToken.expires_at > now,
        )
        .values(used_at=now)
    )
    if claimed.rowcount != 1:
        return None
    user_id = db.scalar(select(PasswordResetToken.user_id).where(PasswordResetToken.token_hash == hashed))
    user = db.get(User, user_id)
    return user if user and user.is_active else None
