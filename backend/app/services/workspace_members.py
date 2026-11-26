"""Workspace-scoped member reads and same-transaction role management."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, cast, delete, func, literal, null, select, union_all, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.authorization import Permission, WorkspaceAccessContext, can_manage_member, has_permission
from app.db.session import auth_read_session
from app.db.workspace_session import workspace_write_session
from app.models import User, Workspace, WorkspaceMember
from app.schemas.workspace_member import MemberListResponse, MemberResponse
from app.services.workspace_access import (
    InsufficientWorkspacePermissions, WorkspaceNotFound, lock_workspace_permission, require_workspace_permission,
)
from app.services.workspaces import scoped_workspace_query


class MemberNotFound(Exception):
    """An authorized manager cannot find the target user or membership."""


class MemberAlreadyExists(Exception):
    """A manager allowed to manage this role attempted a duplicate addition."""


def _public(user_id: UUID, role: str, joined_at: datetime | None) -> MemberResponse:
    return MemberResponse(user_id=user_id, role=role, joined_at=joined_at)


def _member(session: Session, workspace_id: UUID, user_id: UUID):
    return session.execute(select(WorkspaceMember.user_id, WorkspaceMember.role, WorkspaceMember.joined_at).where(
        WorkspaceMember.workspace_id == workspace_id, WorkspaceMember.user_id == user_id,
    )).one_or_none()


def _check_target(access: WorkspaceAccessContext, user_id: UUID, new_role: str | None = None) -> None:
    if user_id in (access.user_id, access.owner_id):
        raise InsufficientWorkspacePermissions
    if new_role == "admin" and not has_permission(access.effective_role, Permission.MEMBER_MANAGE_ADMIN):
        raise InsufficientWorkspacePermissions


def _check_policy(access: WorkspaceAccessContext, user_id: UUID, current_role: str | None, new_role: str | None) -> None:
    if not can_manage_member(access, user_id, current_role=current_role, new_role=new_role):
        raise InsufficientWorkspacePermissions


def _manager_access(session: Session, subject: UUID, workspace_id: UUID, user_id: UUID, role: str | None = None) -> WorkspaceAccessContext:
    # Privacy gate only: reject unauthorized requests before touching target User.
    preliminary = require_workspace_permission(session, subject, workspace_id, Permission.MEMBER_MANAGE_BASIC)
    _check_target(preliminary, user_id, role)
    access = lock_workspace_permission(session, subject, workspace_id, Permission.MEMBER_MANAGE_BASIC,
                                       related_users=(user_id,))
    # The preliminary role is never used as the authority for a write.
    _check_target(access, user_id, role)
    return access


def _active_target_exists(session: Session, user_id: UUID) -> bool:
    return session.scalar(select(User.id).where(User.id == user_id, User.is_active.is_(True))) is not None


def _duplicate_key(error: IntegrityError) -> bool:
    original = error.orig
    return (
        getattr(original, "sqlstate", None) == "23505"
        and getattr(getattr(original, "diag", None), "constraint_name", None) == WorkspaceMember.__table__.primary_key.name
    ) or getattr(original, "sqlite_errorname", None) == "SQLITE_CONSTRAINT_PRIMARYKEY"


def list_members(subject: UUID, workspace_id: UUID, limit: int, offset: int) -> MemberListResponse:
    with auth_read_session() as session:
        require_workspace_permission(session, subject, workspace_id, Permission.MEMBER_READ)
        # Reuse the CRUD scope predicate in each count/page statement.
        visible = scoped_workspace_query(subject).where(Workspace.id == workspace_id).cte("visible_workspace")
        owner = select(
            visible.c.owner_id.label("user_id"), literal("owner").label("role"),
            cast(null(), DateTime(timezone=True)).label("joined_at"), literal(0).label("rank"),
        )
        members = select(
            WorkspaceMember.user_id, WorkspaceMember.role, WorkspaceMember.joined_at, literal(1).label("rank"),
        ).join(visible, WorkspaceMember.workspace_id == visible.c.id).where(
            WorkspaceMember.user_id != visible.c.owner_id,
            WorkspaceMember.role.in_(("admin", "editor", "viewer")),
        )
        scoped = union_all(owner, members).subquery()
        total = session.scalar(select(func.count()).select_from(scoped))
        if not total:
            raise WorkspaceNotFound
        rows = session.execute(select(scoped).order_by(scoped.c.rank, scoped.c.joined_at, scoped.c.user_id)
                               .limit(limit).offset(offset)).mappings()
        response = MemberListResponse(
            items=[_public(row["user_id"], row["role"], row["joined_at"]) for row in rows],
            total=total, limit=limit, offset=offset,
        )
    return response


def add_member(subject: UUID, workspace_id: UUID, user_id: UUID, role: str) -> MemberResponse:
    with workspace_write_session() as session:
        access = _manager_access(session, subject, workspace_id, user_id, role)
        current = _member(session, workspace_id, user_id)
        _check_policy(access, user_id, current.role if current is not None else None, role)
        if current is not None:
            raise MemberAlreadyExists
        if not _active_target_exists(session, user_id):
            raise MemberNotFound
        member = WorkspaceMember(workspace_id=workspace_id, user_id=user_id, role=role)
        session.add(member)
        try:
            session.flush()
        except IntegrityError as error:
            # Only a known primary-key rejection from this pre-commit INSERT.
            if _duplicate_key(error):
                raise MemberAlreadyExists from None
            raise
        session.refresh(member)
        response = _public(member.user_id, member.role, member.joined_at)
    return response


def update_member(subject: UUID, workspace_id: UUID, user_id: UUID, role: str) -> MemberResponse:
    with workspace_write_session() as session:
        access = _manager_access(session, subject, workspace_id, user_id, role)
        current = _member(session, workspace_id, user_id)
        if current is None:
            raise MemberNotFound
        _check_policy(access, user_id, current.role, role)
        if current.role != role:
            changed = session.execute(update(WorkspaceMember).where(
                WorkspaceMember.workspace_id == workspace_id, WorkspaceMember.user_id == user_id,
                WorkspaceMember.role == current.role,
            ).values(role=role))
            if changed.rowcount != 1:
                raise MemberNotFound
        response = _public(user_id, role, current.joined_at)
    return response


def remove_member(subject: UUID, workspace_id: UUID, user_id: UUID) -> None:
    with workspace_write_session() as session:
        access = _manager_access(session, subject, workspace_id, user_id)
        current = _member(session, workspace_id, user_id)
        if current is None:
            raise MemberNotFound
        _check_policy(access, user_id, current.role, None)
        removed = session.execute(delete(WorkspaceMember).where(
            WorkspaceMember.workspace_id == workspace_id, WorkspaceMember.user_id == user_id,
            WorkspaceMember.role == current.role,
        ))
        if removed.rowcount != 1:
            raise MemberNotFound
