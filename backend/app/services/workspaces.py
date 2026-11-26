"""Workspace CRUD, scoped reads and transaction-local authorization locks."""

from uuid import UUID

from sqlalchemy import and_, case, delete, func, or_, select
from sqlalchemy.orm import Session, aliased

from app.core.authorization import Permission, WorkspaceAccessContext, WorkspaceRole
from app.core.token import InvalidAccessToken
from app.db.session import auth_read_session
from app.db.workspace_session import workspace_write_session
from app.models import User, Workspace, WorkspaceMember
from app.schemas.workspace import WorkspaceListResponse, WorkspaceResponse
from app.services.authentication import require_active_identity
from app.services.workspace_access import WorkspaceNotFound, require_workspace_permission


def _lock_account(session: Session, subject: UUID) -> None:
    # FOR SHARE blocks account disable/delete until this transaction finishes.
    user_id = session.scalar(select(User.id).where(User.id == subject).with_for_update(read=True, of=User))
    if user_id is None:
        raise InvalidAccessToken
    require_active_identity(session, subject)


def _lock_access(session: Session, subject: UUID, workspace_id: UUID, permission: Permission) -> WorkspaceAccessContext:
    _lock_account(session, subject)
    # Global protocol: required User rows -> Workspace rows -> member rows.
    locked_id = session.scalar(select(Workspace.id).where(Workspace.id == workspace_id).with_for_update(of=Workspace))
    if locked_id is None:
        raise WorkspaceNotFound
    session.scalar(select(WorkspaceMember.user_id).where(
        WorkspaceMember.workspace_id == workspace_id, WorkspaceMember.user_id == subject,
    ).with_for_update(of=WorkspaceMember))
    # Re-read only after locks: never authorize from a prior dependency/context.
    return require_workspace_permission(session, subject, workspace_id, permission)


def _public(workspace: Workspace, role: WorkspaceRole) -> WorkspaceResponse:
    return WorkspaceResponse(
        id=workspace.id, name=workspace.name, owner_id=workspace.owner_id,
        created_at=workspace.created_at, updated_at=workspace.updated_at, effective_role=role,
    )


def _scoped_query(subject: UUID):
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
        _lock_account(session, subject)
        workspace = Workspace(owner_id=subject, name=name)
        session.add(workspace)
        session.flush()
        session.refresh(workspace)
        response = _public(workspace, WorkspaceRole.OWNER)
    return response


def list_workspaces(subject: UUID, limit: int, offset: int) -> WorkspaceListResponse:
    with auth_read_session() as session:
        require_active_identity(session, subject)
        scoped = _scoped_query(subject)
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
        row = session.execute(_scoped_query(subject).where(Workspace.id == workspace_id)).mappings().one_or_none()
        if row is None:
            raise WorkspaceNotFound
        response = WorkspaceResponse.model_validate(dict(row))
    return response


def update_workspace(subject: UUID, workspace_id: UUID, name: str) -> WorkspaceResponse:
    with workspace_write_session() as session:
        access = _lock_access(session, subject, workspace_id, Permission.WORKSPACE_UPDATE)
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
        access = _lock_access(session, subject, workspace_id, Permission.WORKSPACE_DELETE)
        # Database CASCADE removes memberships; no User is deleted by this SQL.
        deleted = session.execute(delete(Workspace).where(
            Workspace.id == access.workspace_id, Workspace.owner_id == subject,
        ))
        if deleted.rowcount != 1:
            raise WorkspaceNotFound
