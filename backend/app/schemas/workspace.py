"""Strict Workspace inputs and explicit public response contracts."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.core.authorization import WorkspaceRole

MAX_OFFSET = 2**63 - 1


class _WorkspaceNameRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    name: str = Field(strict=True, min_length=1, max_length=150)

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        if not value.strip() or "\x00" in value:
            raise ValueError("Invalid workspace name")
        try:
            value.encode("utf-8")
        except UnicodeEncodeError:
            raise ValueError("Invalid workspace name") from None
        return value


class WorkspaceCreateRequest(_WorkspaceNameRequest):
    """Only name is writable; ownership comes from the verified identity."""


class WorkspaceUpdateRequest(_WorkspaceNameRequest):
    """A name is required; empty PATCH requests are rejected."""


class WorkspaceResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: UUID
    name: str
    owner_id: UUID
    created_at: datetime
    updated_at: datetime
    effective_role: WorkspaceRole


class WorkspaceListResponse(BaseModel):
    items: list[WorkspaceResponse]
    total: int = Field(ge=0)
    limit: int = Field(ge=1, le=100)
    offset: int = Field(ge=0, le=MAX_OFFSET)
