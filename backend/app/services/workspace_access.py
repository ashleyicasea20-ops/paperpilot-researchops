"""Resolve workspace access using the caller's SQLAlchemy Session."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.authorization import Permission, WorkspaceAccessContext, WorkspaceRole, has_permission, parse_member_role
from app.core.token import InvalidAccessToken
from app.models import User, Workspace, WorkspaceMember
from app.services.authentication import require_active_identity


class WorkspaceNotFound(Exception):
    """The workspace does not exist or is inaccessible to this user."""


class InsufficientWorkspacePermissions(Exception):
    """The user can access the workspace but cannot perform the operation."""


def resolve_workspace_access(session: Session, subject: UUID, workspace_id: UUID) -> WorkspaceAccessContext:
    """Use a verified JWT subject and check account state even in a caller-owned transaction."""
    identity = require_active_identity(session, subject)
    with session.no_autoflush:
        owner_id = session.scalar(select(Workspace.owner_id).where(Workspace.id == workspace_id))
        if owner_id is None:
            raise WorkspaceNotFound
        if owner_id == identity.user_id:
            role = WorkspaceRole.OWNER
        else:
            stored_role = session.scalar(select(WorkspaceMember.role).where(
                WorkspaceMember.workspace_id == workspace_id,
                WorkspaceMember.user_id == identity.user_id,
            ))
            role = parse_member_role(stored_role)
            if role is None:
                raise WorkspaceNotFound

    return WorkspaceAccessContext(workspace_id, owner_id, identity.user_id, role)


def require_workspace_permission(
    session: Session, subject: UUID, workspace_id: UUID, permission: Permission,
) -> WorkspaceAccessContext:
    access = resolve_workspace_access(session, subject, workspace_id)
    if not has_permission(access.effective_role, permission):
        raise InsufficientWorkspacePermissions
    return access


def lock_active_accounts(session: Session, subject: UUID, *related_users: UUID) -> None:
    """Lock all participating users before Workspace; disclose only actor state."""
    with session.no_autoflush:
        for user_id in sorted({subject, *related_users}):
            found = session.scalar(select(User.id).where(User.id == user_id).with_for_update(read=True, of=User))
            if user_id == subject and found is None:
                raise InvalidAccessToken
        require_active_identity(session, subject)


def lock_workspace_permission(
    session: Session, subject: UUID, workspace_id: UUID, permission: Permission,
    *, related_users: tuple[UUID, ...] = (),
) -> WorkspaceAccessContext:
    """Caller-owned transaction: sorted User -> Workspace -> sorted member locks."""
    with session.no_autoflush:
        lock_active_accounts(session, subject, *related_users)
        found = session.scalar(select(Workspace.id).where(Workspace.id == workspace_id).with_for_update(of=Workspace))
        if found is None:
            raise WorkspaceNotFound
        for user_id in sorted({subject, *related_users}):
            session.scalar(select(WorkspaceMember.user_id).where(
                WorkspaceMember.workspace_id == workspace_id, WorkspaceMember.user_id == user_id,
            ).with_for_update(of=WorkspaceMember))
        return require_workspace_permission(session, subject, workspace_id, permission)
