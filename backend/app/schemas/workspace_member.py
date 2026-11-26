"""Member inputs and public fields; ownership is a virtual list entry only."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.core.authorization import WorkspaceRole
from app.schemas.workspace import MAX_OFFSET

MemberRole = Literal["admin", "editor", "viewer"]


class MemberRoleRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    role: MemberRole


class MemberCreateRequest(MemberRoleRequest):
    user_id: UUID


class MemberResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    user_id: UUID
    role: WorkspaceRole
    joined_at: datetime | None


class MemberListResponse(BaseModel):
    items: list[MemberResponse]
    total: int = Field(ge=0)
    limit: int = Field(ge=1, le=100)
    offset: int = Field(ge=0, le=MAX_OFFSET)
