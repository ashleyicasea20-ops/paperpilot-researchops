"""Resolve workspace access using the caller's SQLAlchemy Session."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.authorization import Permission, WorkspaceAccessContext, WorkspaceRole, has_permission, parse_member_role
from app.models import Workspace, WorkspaceMember
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
