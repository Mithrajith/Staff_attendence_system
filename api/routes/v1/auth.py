from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from api.core.config import get_settings
from api.core.logs import audit
from api.core.security import create_access_token, hash_password, verify_password
from api.routes.models.auth import (
    ForgotPasswordRequest,
    LoginRequest,
    Message,
    ResetPasswordRequest,
    SignupRequest,
    SignupResponse,
    TokenResponse,
)
from api.services import users as svc
from database.models import Department, Role, User, utcnow
from database.session import get_db

router = APIRouter(prefix="/auth", tags=["auth"])

DB = Annotated[Session, Depends(get_db)]


def _find_by_identifier(db: Session, identifier: str) -> User | None:
    return db.scalar(select(User).where(or_(User.email == identifier, User.username == identifier)))


@router.post("/login", response_model=TokenResponse)
def login(body: LoginRequest, db: DB) -> TokenResponse:
    user = _find_by_identifier(db, body.username)
    # Always runs a hash verification, even for unknown emails, to keep timing uniform.
    if not verify_password(body.password, user.password_hash if user else None):
        audit("login_failed", username=body.username[:100], reason="bad_credentials")
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED, "Incorrect email or password", headers={"WWW-Authenticate": "Bearer"}
        )
    if not user.is_active:
        audit("login_failed", username=body.username[:100], actor_id=user.id, reason="deactivated")
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Account is deactivated")
    user.last_login_at = utcnow()
    db.commit()
    audit("login_success", actor_id=user.id, role=user.role.value)
    minutes = get_settings().kiosk_token_expire_minutes if user.role == Role.system else None
    token, expires_in = create_access_token(user.id, user.token_version, minutes)
    return TokenResponse(access_token=token, expires_in=expires_in)


@router.post("/signup", response_model=SignupResponse, status_code=status.HTTP_201_CREATED)
def signup(body: SignupRequest, db: DB) -> SignupResponse:
    """Public staff self-registration. Returns a token so the client can continue to face enrollment."""
    if not get_settings().signup_enabled:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Sign up is disabled")
    if db.get(Department, body.department_id) is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Unknown department")
    svc.ensure_unique(db, email=body.email, username=body.username, employee_id=body.employee_id)
    user = User(
        email=body.email,
        username=body.username,
        employee_id=body.employee_id,
        department_id=body.department_id,
        full_name=body.full_name or body.username,
        role=Role.staff,
        password_hash=hash_password(body.password),
    )
    db.add(user)
    try:
        db.commit()
    except IntegrityError:  # lost a race with a concurrent signup
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "Username, email or employee ID already registered")
    db.refresh(user)
    audit("signup", actor_id=user.id, username=user.username, employee_id=user.employee_id)
    token, expires_in = create_access_token(user.id, user.token_version)
    return SignupResponse(access_token=token, expires_in=expires_in, user=user)


@router.post("/forgot-password", response_model=Message, status_code=status.HTTP_202_ACCEPTED)
def forgot_password(body: ForgotPasswordRequest, bg: BackgroundTasks, db: DB) -> Message:
    user = _find_by_identifier(db, body.username)
    if user and user.is_active:
        svc.send_password_reset(db, bg, user)
    audit("password_reset_requested", username=body.username[:100], known=bool(user and user.is_active))
    # Same response whether or not the account exists, to avoid email enumeration.
    return Message(detail="If the account exists, a reset link has been sent")


@router.post("/reset-password", response_model=Message)
def reset_password(body: ResetPasswordRequest, db: DB) -> Message:
    user = svc.consume_reset_token(db, body.token)
    if user is None:
        db.rollback()
        audit("password_reset_failed", reason="invalid_or_expired_token")
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid or expired reset token")
    svc.set_password(db, user, body.new_password)
    db.commit()
    audit("password_reset_completed", actor_id=user.id)
    return Message(detail="Password updated. Please sign in with your new password")
