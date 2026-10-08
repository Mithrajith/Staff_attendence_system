from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from api.core.logs import audit
from api.deps import Permission, require
from api.routes.models.department import DepartmentIn, DepartmentOut
from database.models import Department, User
from database.session import get_db

router = APIRouter(prefix="/departments", tags=["departments"])

DB = Annotated[Session, Depends(get_db)]
Admin = Annotated[User, Depends(require(Permission.departments_manage))]


def _get_or_404(db: Session, department_id: int) -> Department:
    dept = db.get(Department, department_id)
    if dept is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Department not found")
    return dept


def _save(db: Session, dept: Department) -> Department:
    db.add(dept)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "Department code or name already exists")
    return dept


@router.get("", response_model=list[DepartmentOut])
def list_departments(db: DB) -> list[Department]:
    """Public: feeds the sign-up dropdown. Only ids and names are exposed."""
    return list(db.scalars(select(Department).order_by(Department.name)))


@router.post("", response_model=DepartmentOut, status_code=status.HTTP_201_CREATED)
def create_department(body: DepartmentIn, db: DB, actor: Admin) -> Department:
    dept = _save(db, Department(code=body.code, name=body.name))
    audit("department_created", actor_id=actor.id, target_id=dept.id, code=dept.code)
    return dept


@router.put("/{department_id}", response_model=DepartmentOut)
def update_department(department_id: int, body: DepartmentIn, db: DB, actor: Admin) -> Department:
    dept = _get_or_404(db, department_id)
    dept.code, dept.name = body.code, body.name
    dept = _save(db, dept)
    audit("department_updated", actor_id=actor.id, target_id=dept.id, code=dept.code)
    return dept


@router.delete("/{department_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_department(department_id: int, db: DB, actor: Admin) -> Response:
    dept = _get_or_404(db, department_id)
    in_use = db.scalar(select(func.count()).select_from(User).where(User.department_id == dept.id))
    if in_use:
        raise HTTPException(status.HTTP_409_CONFLICT, f"Department has {in_use} user(s); reassign them first")
    db.delete(dept)
    db.commit()
    audit("department_deleted", actor_id=actor.id, target_id=department_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
