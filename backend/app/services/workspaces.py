"""Workspace CRUD, scoped reads and transaction-local authorization locks."""

from uuid import UUID

from sqlalchemy import and_, case, delete, func, or_, select
from sqlalchemy.orm import aliased

from app.core.authorization import Permission, WorkspaceRole
from app.db.session import auth_read_session
from app.db.workspace_session import workspace_write_session
from app.models import Workspace, WorkspaceMember
from app.schemas.workspace import WorkspaceListResponse, WorkspaceResponse
from app.services.authentication import require_active_identity
from app.services.workspace_access import (
    WorkspaceNotFound, lock_active_accounts, lock_workspace_permission, require_workspace_permission,
)


def _public(workspace: Workspace, role: WorkspaceRole) -> WorkspaceResponse:
    return WorkspaceResponse(
        id=workspace.id, name=workspace.name, owner_id=workspace.owner_id,
        created_at=workspace.created_at, updated_at=workspace.updated_at, effective_role=role,
    )


def scoped_workspace_query(subject: UUID):
    member = aliased(WorkspaceMember)
    predicate = or_(Workspace.owner_id == subject, member.role.in_(("admin", "editor", "viewer")))
    role = case((Workspace.owner_id == subject, "owner"), else_=member.role).label("effective_role")
    statement = select(
        Workspace.id, Workspace.name, Workspace.owner_id,
        Workspace.created_at, Workspace.updated_at, role,
    ).outerjoin(member, and_(member.workspace_id == Workspace.id, member.user_id == subject)).where(predicate)
    return statement


def create_workspace(subject: UUID, name: str) -> WorkspaceResponse:
    with workspace_write_session() as session:
        lock_active_accounts(session, subject)
        workspace = Workspace(owner_id=subject, name=name)
        session.add(workspace)
        session.flush()
        session.refresh(workspace)
        response = _public(workspace, WorkspaceRole.OWNER)
    return response


def list_workspaces(subject: UUID, limit: int, offset: int) -> WorkspaceListResponse:
    with auth_read_session() as session:
        require_active_identity(session, subject)
        scoped = scoped_workspace_query(subject)
        total = session.scalar(select(func.count()).select_from(scoped.subquery()))
        rows = session.execute(scoped.order_by(Workspace.created_at, Workspace.id).limit(limit).offset(offset)).mappings()
        response = WorkspaceListResponse(
            items=[WorkspaceResponse.model_validate(dict(row)) for row in rows],
            total=total, limit=limit, offset=offset,
        )
    return response


def get_workspace(subject: UUID, workspace_id: UUID) -> WorkspaceResponse:
    with auth_read_session() as session:
        require_workspace_permission(session, subject, workspace_id, Permission.WORKSPACE_READ)
        # Scope the data query too, so a revoked membership cannot bypass it.
        row = session.execute(scoped_workspace_query(subject).where(Workspace.id == workspace_id)).mappings().one_or_none()
        if row is None:
            raise WorkspaceNotFound
        response = WorkspaceResponse.model_validate(dict(row))
    return response


def update_workspace(subject: UUID, workspace_id: UUID, name: str) -> WorkspaceResponse:
    with workspace_write_session() as session:
        access = lock_workspace_permission(session, subject, workspace_id, Permission.WORKSPACE_UPDATE)
        workspace = session.get(Workspace, access.workspace_id, populate_existing=True)
        if workspace is None:
            raise WorkspaceNotFound
        workspace.name = name
        session.flush()
        session.refresh(workspace)
        response = _public(workspace, access.effective_role)
    return response


def delete_workspace(subject: UUID, workspace_id: UUID) -> None:
    with workspace_write_session() as session:
        access = lock_workspace_permission(session, subject, workspace_id, Permission.WORKSPACE_DELETE)
        # Database CASCADE removes memberships; no User is deleted by this SQL.
        deleted = session.execute(delete(Workspace).where(
            Workspace.id == access.workspace_id, Workspace.owner_id == subject,
        ))
        if deleted.rowcount != 1:
            raise WorkspaceNotFound
