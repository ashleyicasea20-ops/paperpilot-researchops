"""Registration contracts. Response fields are an explicit public allowlist."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field, SecretStr, field_validator

from app.core.security import validate_password


class RegisterRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    email: EmailStr
    password: SecretStr = Field(repr=False)

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

    @field_validator("password", mode="before")
    @classmethod
    def check_password(cls, value: object) -> str:
        return validate_password(value)


class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    email: str
    is_active: bool
    created_at: datetime
    updated_at: datetime
