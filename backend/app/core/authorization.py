"""Workspace roles and permissions independent of the HTTP and database layers."""

from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from uuid import UUID


class WorkspaceRole(StrEnum):
    OWNER = "owner"
    ADMIN = "admin"
    EDITOR = "editor"
    VIEWER = "viewer"


class Permission(StrEnum):
    WORKSPACE_READ = "workspace:read"
    WORKSPACE_UPDATE = "workspace:update"
    WORKSPACE_DELETE = "workspace:delete"
    WORKSPACE_TRANSFER = "workspace:transfer"
    RESOURCE_READ = "resource:read"
    RESOURCE_CREATE = "resource:create"
    RESOURCE_UPDATE = "resource:update"
    RESOURCE_DELETE = "resource:delete"
    MEMBER_READ = "member:read"
    MEMBER_MANAGE_BASIC = "member:manage_basic"
    MEMBER_MANAGE_ADMIN = "member:manage_admin"


_VIEWER_PERMISSIONS = frozenset({Permission.WORKSPACE_READ, Permission.RESOURCE_READ, Permission.MEMBER_READ})
_EDITOR_PERMISSIONS = _VIEWER_PERMISSIONS | {Permission.RESOURCE_CREATE, Permission.RESOURCE_UPDATE}
_ADMIN_PERMISSIONS = _EDITOR_PERMISSIONS | {
    Permission.WORKSPACE_UPDATE, Permission.RESOURCE_DELETE, Permission.MEMBER_MANAGE_BASIC,
}
ROLE_PERMISSIONS = MappingProxyType({
    WorkspaceRole.VIEWER: _VIEWER_PERMISSIONS,
    WorkspaceRole.EDITOR: _EDITOR_PERMISSIONS,
    WorkspaceRole.ADMIN: _ADMIN_PERMISSIONS,
    WorkspaceRole.OWNER: _ADMIN_PERMISSIONS | {
        Permission.WORKSPACE_DELETE, Permission.WORKSPACE_TRANSFER, Permission.MEMBER_MANAGE_ADMIN,
    },
})


@dataclass(frozen=True, slots=True)
class WorkspaceAccessContext:
    """The role resolved for one authenticated user and workspace."""

    workspace_id: UUID
    owner_id: UUID
    user_id: UUID
    effective_role: WorkspaceRole


def parse_member_role(value: object) -> WorkspaceRole | None:
    """Resolve only roles that may be stored in a membership row."""
    if not isinstance(value, str):
        return None
    try:
        role = WorkspaceRole(value)
    except ValueError:
        return None
    return role if role in (WorkspaceRole.ADMIN, WorkspaceRole.EDITOR, WorkspaceRole.VIEWER) else None


def has_permission(role: WorkspaceRole, permission: Permission) -> bool:
    if not isinstance(role, WorkspaceRole) or not isinstance(permission, Permission):
        return False
    return permission in ROLE_PERMISSIONS.get(role, ())


def can_manage_member(
    access: WorkspaceAccessContext,
    target_user_id: UUID,
    *,
    current_role: str | None,
    new_role: str | None,
) -> bool:
    """Evaluate both role values. None means absent membership or removal."""
    if (access.effective_role is WorkspaceRole.OWNER) != (access.user_id == access.owner_id):
        return False
    if target_user_id in (access.owner_id, access.user_id) or (current_role is None and new_role is None):
        return False

    for value in (current_role, new_role):
        if value is None:
            continue
        role = parse_member_role(value)
        if role is None:
            return False
        permission = Permission.MEMBER_MANAGE_ADMIN if role is WorkspaceRole.ADMIN else Permission.MEMBER_MANAGE_BASIC
        if not has_permission(access.effective_role, permission):
            return False
    return True
