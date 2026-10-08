from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, EmailStr, Field, StringConstraints, field_validator

from database.models import Role

# Argon2 handles long inputs; the cap just bounds hashing cost per request.
Password = Annotated[str, StringConstraints(min_length=10, max_length=128)]


class UserCreate(BaseModel):
    email: EmailStr
    full_name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]
    role: Role = Role.staff
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
    full_name: str
    role: Role
    is_active: bool
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
