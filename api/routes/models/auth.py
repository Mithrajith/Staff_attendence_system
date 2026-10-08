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
    username: Username
    employee_id: EmployeeId
    department_id: int
    email: EmailStr  # needed for password reset emails
    password: Password
    full_name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)] | None = None

    @field_validator("email")
    @classmethod
    def _lower(cls, v: str) -> str:
        return v.lower()


class SignupResponse(TokenResponse):
    user: UserOut


class ForgotPasswordRequest(BaseModel):
    username: Identifier


class ResetPasswordRequest(BaseModel):
    token: str = Field(min_length=20, max_length=256)
    new_password: Password


class Message(BaseModel):
    detail: str
