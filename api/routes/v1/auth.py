from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from api.core.security import create_access_token, verify_password
from api.routes.models.auth import (
    ForgotPasswordRequest,
    LoginRequest,
    Message,
    ResetPasswordRequest,
    TokenResponse,
)
from api.services import users as svc
from database.models import User, utcnow
from database.session import get_db

router = APIRouter(prefix="/auth", tags=["auth"])

DB = Annotated[Session, Depends(get_db)]


@router.post("/login", response_model=TokenResponse)
def login(body: LoginRequest, db: DB) -> TokenResponse:
    user = db.scalar(select(User).where(User.email == body.email))
    # Always runs a hash verification, even for unknown emails, to keep timing uniform.
    if not verify_password(body.password, user.password_hash if user else None):
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED, "Incorrect email or password", headers={"WWW-Authenticate": "Bearer"}
        )
    if not user.is_active:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Account is deactivated")
    user.last_login_at = utcnow()
    db.commit()
    token, expires_in = create_access_token(user.id, user.token_version)
    return TokenResponse(access_token=token, expires_in=expires_in)


@router.post("/forgot-password", response_model=Message, status_code=status.HTTP_202_ACCEPTED)
def forgot_password(body: ForgotPasswordRequest, bg: BackgroundTasks, db: DB) -> Message:
    user = db.scalar(select(User).where(User.email == body.email))
    if user and user.is_active:
        svc.send_password_reset(db, bg, user)
    # Same response whether or not the account exists, to avoid email enumeration.
    return Message(detail="If the account exists, a reset link has been sent")


@router.post("/reset-password", response_model=Message)
def reset_password(body: ResetPasswordRequest, db: DB) -> Message:
    user = svc.consume_reset_token(db, body.token)
    if user is None:
        db.rollback()
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid or expired reset token")
    svc.set_password(db, user, body.new_password)
    db.commit()
    return Message(detail="Password updated. Please sign in with your new password")
