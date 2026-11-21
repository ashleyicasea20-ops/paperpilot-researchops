"""Authentication contracts and explicit public response fields."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field, SecretStr, field_validator

from app.core.security import MAX_PASSWORD_BYTES, validate_password


class _EmailRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    email: EmailStr

    @field_validator("email", mode="before")
    @classmethod
    def prepare_email(cls, value: object) -> str:
        if not isinstance(value, str):
            raise ValueError("Invalid email")
        value = value.strip()
        if not value.isascii() or "<" in value or ">" in value:
            raise ValueError("Invalid email")
        return value

    @field_validator("email", mode="after")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        value = str(value).lower()
        if not value.isascii() or len(value) > 320:
            raise ValueError("Invalid email")
        return value


class RegisterRequest(_EmailRequest):
    password: SecretStr = Field(repr=False)

    @field_validator("password", mode="before")
    @classmethod
    def check_password(cls, value: object) -> str:
        return validate_password(value)


class LoginRequest(_EmailRequest):
    password: SecretStr = Field(repr=False)

    @field_validator("password", mode="before")
    @classmethod
    def check_password(cls, value: object) -> str:
        if not isinstance(value, str) or not 1 <= len(value) <= MAX_PASSWORD_BYTES:
            raise ValueError("Invalid password")
        try:
            size = len(value.encode("utf-8"))
        except UnicodeEncodeError:
            raise ValueError("Invalid password") from None
        if size > MAX_PASSWORD_BYTES:
            raise ValueError("Invalid password")
        return value


class TokenResponse(BaseModel):
    access_token: str = Field(repr=False)
    token_type: Literal["bearer"] = "bearer"
    expires_in: int


class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    email: str
    is_active: bool
    created_at: datetime
    updated_at: datetime
