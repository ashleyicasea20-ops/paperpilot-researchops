"""User identity storage, without authentication or password services."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import Boolean, CheckConstraint, Index, String, func, true
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin, WHITESPACE_CHARACTERS

if TYPE_CHECKING:
    from app.models.workspace import Workspace
    from app.models.workspace_member import WorkspaceMember


class User(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A user may own workspaces and collaborate through memberships."""

    __tablename__ = "users"

    email: Mapped[str] = mapped_column(String(320), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=true()
    )

    __table_args__ = (
        Index("uq_users_email_lower", func.lower(email), unique=True),
        CheckConstraint(
            func.length(func.trim(email, WHITESPACE_CHARACTERS)) > 0,
            name="email_not_blank",
        ),
        CheckConstraint(
            func.length(func.trim(password_hash, WHITESPACE_CHARACTERS)) > 0,
            name="password_hash_not_blank",
        ),
    )

    # Let RESTRICT reject deletion, including when owned objects are loaded.
    owned_workspaces: Mapped[list[Workspace]] = relationship(
        back_populates="owner", passive_deletes="all"
    )
    workspace_memberships: Mapped[list[WorkspaceMember]] = relationship(
        back_populates="user", cascade="all, delete-orphan", passive_deletes=True
    )
