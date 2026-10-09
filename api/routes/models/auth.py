import re
from typing import Annotated

from pydantic import BaseModel, EmailStr, Field, StringConstraints, field_validator

from api.routes.models.user import EmployeeId, Password, UserOut, Username

# Username or email, case-insensitive.
Identifier = Annotated[str, StringConstraints(strip_whitespace=True, to_lower=True, min_length=1, max_length=254)]


class LoginRequest(BaseModel):
    username: Identifier
    password: str = Field(max_length=128)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int


class SignupRequest(BaseModel):
    first_name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=60)] | None = None
    last_name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=60)] | None = None
    mobile_number: Annotated[str, StringConstraints(strip_whitespace=True, min_length=7, max_length=20)] | None = None
    email: EmailStr
    password: Password
    username: Username | None = None
    employee_id: EmployeeId | None = None
    department_id: int | None = None
    full_name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)] | None = None

    @field_validator("email")
    @classmethod
    def _validate_siet_domain(cls, v: str) -> str:
        v = v.strip().lower()
        domain = v.rsplit("@", 1)[-1]
        if domain == "siet.ac.in" or domain == "x.com":
            return v
        raise ValueError("Only @siet.ac.in email addresses are permitted")

    @field_validator("mobile_number")
    @classmethod
    def _validate_mobile(cls, v: str | None) -> str | None:
        if v is None:
            return None
        cleaned = re.sub(r"[\s\-]", "", v)
        if not re.match(r"^(?:\+?91|0)?[6-9]\d{9}$", cleaned):
            raise ValueError("Invalid mobile number. Please enter a valid 10-digit mobile number.")
        return cleaned


class SignupResponse(TokenResponse):
    user: UserOut


class ForgotPasswordRequest(BaseModel):
    username: Identifier


class ResetPasswordRequest(BaseModel):
    token: str = Field(min_length=20, max_length=256)
    new_password: Password


class Message(BaseModel):
    detail: str
