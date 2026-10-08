from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Response, status
from sqlalchemy import delete, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from api.core.security import generate_temp_password, hash_password, verify_password
from api.deps import CurrentUser, Permission, get_user_or_404, has_permission, require
from api.routes.models.user import PasswordChange, UserCreate, UserList, UserOut
from api.services import users as svc
from database.models import PasswordResetToken, Role, User
from database.session import get_db

router = APIRouter(prefix="/users", tags=["users"])

DB = Annotated[Session, Depends(get_db)]


def _self_or(permission: Permission, user: CurrentUser, user_id: int) -> None:
    if user.id != user_id and not has_permission(user, permission):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Insufficient permissions")


@router.post(
    "",
    response_model=UserOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require(Permission.users_create))],
)
def create_user(body: UserCreate, bg: BackgroundTasks, db: DB) -> User:
    user = User(
        email=body.email,
        full_name=body.full_name,
        role=body.role,
        password_hash=hash_password(body.password or generate_temp_password()),
    )
    db.add(user)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "A user with this email already exists")
    if body.password is None:
        svc.send_welcome(db, bg, user)
    return user


@router.get("", response_model=UserList, dependencies=[Depends(require(Permission.users_read_any))])
def list_users(
    db: DB,
    role: Role | None = None,
    is_active: bool | None = None,
    q: Annotated[str | None, Query(max_length=100)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> UserList:
    stmt = select(User)
    if role is not None:
        stmt = stmt.where(User.role == role)
    if is_active is not None:
        stmt = stmt.where(User.is_active == is_active)
    if q:
        stmt = stmt.where(or_(User.email.contains(q, autoescape=True), User.full_name.contains(q, autoescape=True)))
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    items = db.scalars(stmt.order_by(User.id).limit(limit).offset(offset)).all()
    return UserList(items=items, total=total, limit=limit, offset=offset)


@router.get("/me", response_model=UserOut)
def me(user: CurrentUser) -> User:
    return user


@router.get("/{user_id}", response_model=UserOut)
def get_user(user_id: int, user: CurrentUser, db: DB) -> User:
    _self_or(Permission.users_read_any, user, user_id)
    return get_user_or_404(db, user_id)


def _set_active(user_id: int, active: bool, actor: User, db: Session) -> User:
    if actor.id == user_id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "You cannot change your own active status")
    target = get_user_or_404(db, user_id)
    target.is_active = active
    db.commit()
    return target


@router.post("/{user_id}/activate", response_model=UserOut)
def activate_user(user_id: int, db: DB, actor: Annotated[User, Depends(require(Permission.users_manage))]) -> User:
    return _set_active(user_id, True, actor, db)


@router.post("/{user_id}/deactivate", response_model=UserOut)
def deactivate_user(user_id: int, db: DB, actor: Annotated[User, Depends(require(Permission.users_manage))]) -> User:
    return _set_active(user_id, False, actor, db)


@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_user(user_id: int, db: DB, actor: Annotated[User, Depends(require(Permission.users_manage))]) -> Response:
    if actor.id == user_id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "You cannot delete your own account")
    target = get_user_or_404(db, user_id)
    db.execute(delete(PasswordResetToken).where(PasswordResetToken.user_id == target.id))
    db.delete(target)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.put("/{user_id}/password", status_code=status.HTTP_204_NO_CONTENT)
def change_password(user_id: int, body: PasswordChange, actor: CurrentUser, db: DB) -> Response:
    """Self: requires current_password. Admin changing someone else's: no current password needed.

    Either way existing access tokens for the target are revoked.
    """
    if actor.id == user_id:
        if not body.current_password or not verify_password(body.current_password, actor.password_hash):
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Current password is incorrect")
        target = actor
    else:
        if not has_permission(actor, Permission.users_set_password_any):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Insufficient permissions")
        target = get_user_or_404(db, user_id)
    svc.set_password(db, target, body.new_password)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
