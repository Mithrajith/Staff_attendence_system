from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, EmailStr, Field, StringConstraints, field_validator

from api.routes.models.department import DepartmentOut
from database.models import Role

# Argon2 handles long inputs; the cap just bounds hashing cost per request.
Password = Annotated[str, StringConstraints(min_length=10, max_length=128)]
Username = Annotated[str, StringConstraints(strip_whitespace=True, to_lower=True, pattern=r"^[A-Za-z0-9._-]{3,32}$")]
EmployeeId = Annotated[str, StringConstraints(strip_whitespace=True, pattern=r"^[A-Za-z0-9_-]{1,32}$")]


class UserCreate(BaseModel):
    email: EmailStr
    full_name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]
    role: Role = Role.staff
    username: Username | None = None
    employee_id: EmployeeId | None = None
    department_id: int | None = None
    mobile_number: Annotated[str, StringConstraints(strip_whitespace=True, min_length=7, max_length=20)] | None = None
    # Optional: if omitted, a random password is set and the user gets an email link to choose their own.
    password: Password | None = None

    @field_validator("email")
    @classmethod
    def _lower(cls, v: str) -> str:
        return v.lower()


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    email: EmailStr
    username: str | None
    employee_id: str | None
    mobile_number: str | None = None
    department: DepartmentOut | None
    full_name: str
    role: Role
    is_active: bool
    face_enrolled: bool
    created_at: datetime
    last_login_at: datetime | None


class UserList(BaseModel):
    items: list[UserOut]
    total: int
    limit: int
    offset: int


class PasswordChange(BaseModel):
    new_password: Password
    # Required when a user changes their own password; ignored for admins acting on other users.
    current_password: str | None = Field(default=None, max_length=128)
