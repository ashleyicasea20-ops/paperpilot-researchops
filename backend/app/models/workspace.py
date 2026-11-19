"""Workspace ownership and collaboration boundary."""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import CheckConstraint, ForeignKey, String, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin, WHITESPACE_CHARACTERS

if TYPE_CHECKING:
    from app.models.user import User
    from app.models.workspace_member import WorkspaceMember


class Workspace(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """owner_id is the single authority for workspace ownership."""

    __tablename__ = "workspaces"

    owner_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(150), nullable=False)

    __table_args__ = (
        CheckConstraint(
            func.length(func.trim(name, WHITESPACE_CHARACTERS)) > 0,
            name="name_not_blank",
        ),
    )

    owner: Mapped[User] = relationship(back_populates="owned_workspaces")
    memberships: Mapped[list[WorkspaceMember]] = relationship(
        back_populates="workspace", cascade="all, delete-orphan", passive_deletes=True
    )
