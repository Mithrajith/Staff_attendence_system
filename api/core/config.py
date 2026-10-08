"""Environment-driven settings. Secrets are never defaulted in production."""

from functools import lru_cache
from typing import Literal
from urllib.parse import quote_plus

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_PLACEHOLDER_MARKERS = ("change-me", "changeme", "some-random", "your-")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: Literal["development", "production"] = "development"
    api_prefix: str = "/api/v1"
    cors_origins: list[str] = []

    # MySQL. DATABASE_URL wins if set (managed DBs, tests).
    database_url: SecretStr | None = None
    mysql_host: str = "localhost"
    mysql_port: int = 3306
    mysql_user: str = "staff_app"
    mysql_password: SecretStr | None = None
    mysql_database: str = "staff_attendance"
    mysql_ssl_ca: str | None = None
    db_pool_size: int = 10
    db_max_overflow: int = 20
    db_pool_recycle: int = 1800

    # Auth
    jwt_secret_key: SecretStr
    jwt_algorithm: Literal["HS256", "HS384", "HS512"] = "HS256"
    access_token_expire_minutes: int = Field(30, ge=1)
    password_reset_expire_minutes: int = Field(30, ge=5)

    # Email. "console" only logs the message (development); production must use smtp.
    email_backend: Literal["console", "smtp"] = "console"
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_user: str | None = None
    smtp_password: SecretStr | None = None
    smtp_use_ssl: bool = False  # implicit TLS (port 465); otherwise STARTTLS is enforced
    email_from: str = "Staff Attendance <no-reply@localhost>"
    # Base URL used in emails: "<frontend_url>/reset-password?token=..."
    frontend_url: str = "http://localhost:3000"

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"

    @property
    def sqlalchemy_url(self) -> str:
        if self.database_url:
            return self.database_url.get_secret_value()
        password = self.mysql_password.get_secret_value() if self.mysql_password else ""
        return (
            f"mysql+pymysql://{quote_plus(self.mysql_user)}:{quote_plus(password)}"
            f"@{self.mysql_host}:{self.mysql_port}/{self.mysql_database}?charset=utf8mb4"
        )

    @model_validator(mode="after")
    def _validate(self) -> "Settings":
        jwt_secret = self.jwt_secret_key.get_secret_value()
        if len(jwt_secret) < 32:
            raise ValueError("JWT_SECRET_KEY must be at least 32 characters")
        if not self.is_production:
            return self
        secrets = [jwt_secret]
        if self.mysql_password:
            secrets.append(self.mysql_password.get_secret_value())
        if any(m in s.lower() for s in secrets for m in _PLACEHOLDER_MARKERS):
            raise ValueError("Placeholder secret detected; set real values for production")
        if not self.database_url and not self.mysql_password:
            raise ValueError("MYSQL_PASSWORD (or DATABASE_URL) is required in production")
        if self.email_backend != "smtp" or not self.smtp_host:
            raise ValueError("EMAIL_BACKEND=smtp and SMTP_HOST are required in production")
        if "*" in self.cors_origins:
            raise ValueError("Wildcard CORS origin is not allowed in production")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
