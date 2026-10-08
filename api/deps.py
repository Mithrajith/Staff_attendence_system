"""Authentication + role-based permissions.

Admins hold every permission. Staff hold none: they only reach their own data, which route
handlers allow via an explicit "is self" check. The `system` role is intentionally empty until
its permissions are defined: add them to ROLE_PERMISSIONS.
"""

from enum import Enum
from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from api.core.security import decode_access_token
from database.models import Role, User
from database.session import get_db


class Permission(str, Enum):
    users_create = "users:create"
    users_read_any = "users:read_any"
    users_manage = "users:manage"  # activate / deactivate / delete
    users_set_password_any = "users:set_password_any"
    attendance_mark_self = "attendance:mark_self"  # check in / out as yourself
    attendance_read_any = "attendance:read_any"  # all users' records and raw events


ROLE_PERMISSIONS: dict[Role, frozenset[Permission]] = {
    Role.admin: frozenset(Permission),
    Role.system: frozenset(),  # TBD
    Role.staff: frozenset({Permission.attendance_mark_self}),
}

_bearer = HTTPBearer(auto_error=False)
_unauthorized = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Invalid or expired credentials",
    headers={"WWW-Authenticate": "Bearer"},
)


def get_current_user(
    creds: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    db: Annotated[Session, Depends(get_db)],
) -> User:
    if creds is None:
        raise _unauthorized
    payload = decode_access_token(creds.credentials)
    if payload is None or not str(payload["sub"]).isdigit():
        raise _unauthorized
    user = db.get(User, int(payload["sub"]))
    # Checked on every request so deactivation and password changes revoke existing tokens immediately.
    if user is None or not user.is_active or payload["tv"] != user.token_version:
        raise _unauthorized
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


def has_permission(user: User, permission: Permission) -> bool:
    return permission in ROLE_PERMISSIONS[user.role]


def require(permission: Permission):
    def checker(user: CurrentUser) -> User:
        if not has_permission(user, permission):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Insufficient permissions")
        return user

    return checker


def get_user_or_404(db: Session, user_id: int) -> User:
    user = db.scalar(select(User).where(User.id == user_id))
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    return user
