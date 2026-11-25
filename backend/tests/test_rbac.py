"""Exhaustive workspace permission checks and member-management policy."""

from uuid import uuid4

import pytest

from app.core.authorization import (
    ROLE_PERMISSIONS, Permission, WorkspaceAccessContext, WorkspaceRole,
    can_manage_member, has_permission, parse_member_role,
)

OWNER, ADMIN, EDITOR, VIEWER = WorkspaceRole.OWNER, WorkspaceRole.ADMIN, WorkspaceRole.EDITOR, WorkspaceRole.VIEWER

EXPECTED_PERMISSIONS = {
    OWNER: {
        "workspace:read", "workspace:update", "workspace:delete", "workspace:transfer",
        "resource:read", "resource:create", "resource:update", "resource:delete",
        "member:read", "member:manage_basic", "member:manage_admin",
    },
    ADMIN: {
        "workspace:read", "workspace:update", "resource:read", "resource:create", "resource:update",
        "resource:delete", "member:read", "member:manage_basic",
    },
    EDITOR: {"workspace:read", "resource:read", "resource:create", "resource:update", "member:read"},
    VIEWER: {"workspace:read", "resource:read", "member:read"},
}


@pytest.mark.parametrize("role", WorkspaceRole)
@pytest.mark.parametrize("permission", Permission)
def test_explicit_permission_matrix(role, permission):
    assert has_permission(role, permission) is (permission.value in EXPECTED_PERMISSIONS[role])


def test_permission_values_and_mapping_are_immutable():
    assert {p.value for p in Permission} == EXPECTED_PERMISSIONS[OWNER]
    assert set(ROLE_PERMISSIONS) == set(WorkspaceRole)
    with pytest.raises(TypeError):
        ROLE_PERMISSIONS[VIEWER] = ROLE_PERMISSIONS[OWNER]
    with pytest.raises(AttributeError):
        ROLE_PERMISSIONS[VIEWER].add(Permission.WORKSPACE_DELETE)


@pytest.mark.parametrize(("role", "permission"), [
    ("unknown", Permission.WORKSPACE_READ), (None, Permission.WORKSPACE_READ),
    ("owner", Permission.WORKSPACE_DELETE), (OWNER, "workspace:unknown"), (OWNER, "workspace:read"),
])
def test_permission_check_rejects_unknown_or_untagged_values(role, permission):
    assert not has_permission(role, permission)


@pytest.mark.parametrize("role", ["admin", "editor", "viewer"])
def test_parse_ordinary_member_role(role):
    assert parse_member_role(role) is WorkspaceRole(role)


@pytest.mark.parametrize("role", [None, 0, "", "owner", "superuser", "ADMIN", "viewer "])
def test_invalid_database_roles_fail_closed(role):
    assert parse_member_role(role) is None


@pytest.mark.parametrize(("actor", "current", "proposed", "allowed"), [
    (ADMIN, None, "viewer", True), (ADMIN, None, "editor", True),
    (ADMIN, "viewer", "editor", True), (ADMIN, "editor", "viewer", True),
    (ADMIN, "editor", None, True), (ADMIN, "viewer", None, True),
    (ADMIN, None, "admin", False), (ADMIN, "editor", "admin", False),
    (ADMIN, "admin", "editor", False), (ADMIN, "admin", None, False),
    (EDITOR, None, "viewer", False), (EDITOR, "viewer", None, False),
    (VIEWER, None, "viewer", False), (VIEWER, "editor", "viewer", False),
    (OWNER, None, "admin", True), (OWNER, "admin", "viewer", True),
    (OWNER, "viewer", "editor", True), (OWNER, "editor", None, True),
    (OWNER, "admin", None, True), (OWNER, "viewer", "owner", False),
    (ADMIN, "viewer", "owner", False), (OWNER, "owner", None, False),
    (OWNER, "unknown", "editor", False), (ADMIN, "editor", "unknown", False),
    (OWNER, None, None, False),
])
def test_member_management_checks_current_and_proposed_roles(actor, current, proposed, allowed):
    owner_id = uuid4()
    actor_id = owner_id if actor is OWNER else uuid4()
    access = WorkspaceAccessContext(uuid4(), owner_id, actor_id, actor)
    assert can_manage_member(access, uuid4(), current_role=current, new_role=proposed) is allowed


@pytest.mark.parametrize("actor", WorkspaceRole)
def test_member_management_rejects_owner_and_self_as_targets(actor):
    owner_id = uuid4()
    actor_id = owner_id if actor is OWNER else uuid4()
    access = WorkspaceAccessContext(uuid4(), owner_id, actor_id, actor)
    assert not can_manage_member(access, owner_id, current_role="viewer", new_role=None)
    assert not can_manage_member(access, actor_id, current_role="viewer", new_role="admin")


def test_member_management_denies_inconsistent_owner_context():
    access = WorkspaceAccessContext(uuid4(), uuid4(), uuid4(), OWNER)
    assert not can_manage_member(access, uuid4(), current_role="viewer", new_role="admin")
