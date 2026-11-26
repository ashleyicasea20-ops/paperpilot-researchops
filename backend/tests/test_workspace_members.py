"""Production membership API, persisted RBAC results and public contracts."""

from datetime import datetime, timezone
from itertools import product
from uuid import uuid4

import pytest
from sqlalchemy import event, select, text
from sqlalchemy.exc import IntegrityError

from app.main import app
from app.models import User, Workspace, WorkspaceMember

FIELDS = {"user_id", "role", "joined_at"}


@pytest.mark.parametrize("actor", ["A", "B", "C", "D", "E", "F"])
def test_read_members_permissions_and_virtual_owner(member_api, workspace_tenants, actor):
    response = member_api("GET", user=actor)
    expected = 404 if actor in ("B", "F") else 200
    assert response.status_code == expected
    assert response.headers["cache-control"] == "no-store"
    if expected == 200:
        data = response.json()
        assert data["total"] == 4 and data["limit"] == 20 and data["offset"] == 0
        assert data["items"][0] == {"user_id": str(workspace_tenants.users["A"]), "role": "owner", "joined_at": None}
        assert all(set(row) == FIELDS for row in data["items"])


@pytest.mark.parametrize("actor,role", list(product(["A", "C", "D", "E", "F"], ["admin", "editor", "viewer"])))
def test_add_role_matrix_persists_only_authorized_members(member_api, workspace_database, workspace_tenants, actor, role):
    response = member_api("POST", user=actor, json={"user_id": str(workspace_tenants.users["F"]), "role": role})
    expected = 201 if actor == "A" or (actor == "C" and role != "admin") else 404 if actor == "F" else 403
    assert response.status_code == expected
    with workspace_database() as check:
        member = check.get(WorkspaceMember, (workspace_tenants.spaces["A"], workspace_tenants.users["F"]))
        assert (member.role if member else None) == (role if expected == 201 else None)
        assert check.get(WorkspaceMember, (workspace_tenants.spaces["A"], workspace_tenants.users["A"])) is None
    if expected == 201:
        assert set(response.json()) == FIELDS and response.json()["role"] == role
        assert datetime.fromisoformat(response.json()["joined_at"])


@pytest.mark.parametrize("actor,target,role", list(product(["A", "C", "D", "E", "F"], ["C", "D", "E"], ["admin", "editor", "viewer"])))
def test_patch_checks_both_stored_and_requested_roles(member_api, workspace_database, workspace_tenants, actor, target, role):
    original = {"C": "admin", "D": "editor", "E": "viewer"}[target]
    allowed = actor != target and (actor == "A" or (actor == "C" and original != "admin" and role != "admin"))
    response = member_api("PATCH", user=actor, target=target, json={"role": role})
    assert response.status_code == (200 if allowed else 404 if actor == "F" else 403)
    with workspace_database() as check:
        assert check.get(WorkspaceMember, (workspace_tenants.spaces["A"], workspace_tenants.users[target])).role == (role if allowed else original)


@pytest.mark.parametrize("actor,target", list(product(["A", "C", "D", "E", "F"], ["C", "D", "E"])))
def test_remove_role_matrix_preserves_users_and_spaces(member_api, workspace_database, workspace_tenants, actor, target):
    allowed = actor != target and (actor == "A" or (actor == "C" and target != "C"))
    response = member_api("DELETE", user=actor, target=target)
    assert response.status_code == (204 if allowed else 404 if actor == "F" else 403)
    with workspace_database() as check:
        assert (check.get(WorkspaceMember, (workspace_tenants.spaces["A"], workspace_tenants.users[target])) is None) == allowed
        assert len(list(check.scalars(select(User)))) == 6
        assert len(list(check.scalars(select(Workspace)))) == 2
        assert check.get(WorkspaceMember, (workspace_tenants.spaces["B"], workspace_tenants.users["C"])).role == "viewer"
    if allowed:
        assert response.content == b""


@pytest.mark.parametrize("actor", ["A", "C"])
@pytest.mark.parametrize("method", ["POST", "PATCH", "DELETE"])
@pytest.mark.parametrize("target", ["A", "self"])
def test_owner_and_self_cannot_be_managed(member_api, workspace_tenants, actor, method, target):
    user_id = workspace_tenants.users[actor if target == "self" else target]
    body = {"role": "viewer"}
    if method == "POST":
        body["user_id"] = str(user_id)
    response = member_api(method, user=actor, target=user_id if method != "POST" else None,
                          **({"json": body} if method != "DELETE" else {}))
    assert response.status_code == 403


@pytest.mark.parametrize("method", ["POST", "PATCH"])
@pytest.mark.parametrize("role", ["owner", "ADMIN", " viewer", "", None, True, 1, [], {}])
def test_invalid_roles_are_422_and_redacted(member_api, workspace_tenants, method, role):
    body = {"role": role}
    if method == "POST":
        body["user_id"] = str(workspace_tenants.users["F"])
    response = member_api(method, target="E" if method == "PATCH" else None, json=body)
    assert response.status_code == 422 and response.json() == {"detail": "Invalid member input"}


@pytest.mark.parametrize("method", ["POST", "PATCH"])
@pytest.mark.parametrize("field", ["owner_id", "actor_id", "current_role", "workspace_id", "joined_at", "password_hash"])
def test_unknown_body_fields_cannot_supply_identity_or_current_role(member_api, workspace_tenants, method, field):
    body = {"role": "viewer", field: "synthetic-private-input"}
    if method == "POST":
        body["user_id"] = str(workspace_tenants.users["F"])
    response = member_api(method, target="E" if method == "PATCH" else None, json=body)
    assert response.status_code == 422 and "synthetic-private-input" not in response.text


@pytest.mark.parametrize("method", ["POST", "PATCH"])
@pytest.mark.parametrize("body", [{}, [], None])
def test_required_body_fields(member_api, method, body):
    assert member_api(method, target="E" if method == "PATCH" else None, json=body).status_code == 422


def test_duplicate_and_missing_targets_are_fixed_errors(member_api, workspace_tenants):
    assert member_api("POST", json={"user_id": str(workspace_tenants.users["D"]), "role": "viewer"}).json() == {"detail": "Member already exists"}
    assert member_api("POST", json={"user_id": str(workspace_tenants.users["D"]), "role": "viewer"}).status_code == 409
    for method in ("PATCH", "DELETE"):
        response = member_api(method, target="F", **({"json": {"role": "editor"}} if method == "PATCH" else {}))
        assert response.status_code == 404 and response.json() == {"detail": "Member not found"}
    missing = member_api("POST", json={"user_id": str(uuid4()), "role": "viewer"})
    assert missing.status_code == 404 and missing.json() == {"detail": "Member not found"}


@pytest.mark.parametrize("method", ["POST", "PATCH", "DELETE"])
def test_admin_cannot_manage_another_admin(member_api, workspace_database, workspace_tenants, method):
    with workspace_database.begin() as session:
        session.add(WorkspaceMember(workspace_id=workspace_tenants.spaces["A"], user_id=workspace_tenants.users["B"], role="admin"))
    body = {"role": "viewer"}
    if method == "POST":
        body["user_id"] = str(workspace_tenants.users["B"])
    response = member_api(method, user="C", target="B" if method != "POST" else None,
                          **({"json": body} if method != "DELETE" else {}))
    assert response.status_code == 403
    with workspace_database() as session:
        assert session.get(WorkspaceMember, (workspace_tenants.spaces["A"], workspace_tenants.users["B"])).role == "admin"


def test_same_role_patch_is_authorized_noop(member_api, workspace_database, workspace_tenants):
    writes = []
    engine = workspace_database.kw["bind"]
    def record(conn, cursor, statement, parameters, context, many):
        if statement.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE")):
            writes.append(statement)
    event.listen(engine, "before_cursor_execute", record)
    try:
        result = member_api("PATCH", target="E", json={"role": "viewer"})
    finally:
        event.remove(engine, "before_cursor_execute", record)
    assert result.status_code == 200 and writes == []
    with workspace_database() as check:
        assert datetime.fromisoformat(result.json()["joined_at"]) == check.get(WorkspaceMember, (workspace_tenants.spaces["A"], workspace_tenants.users["E"])).joined_at
    assert member_api("PATCH", user="C", target="C", json={"role": "admin"}).status_code == 403


def test_stable_owner_first_pagination_and_deduplication(member_api, workspace_database, workspace_tenants):
    timestamp = datetime(2000, 1, 1, tzinfo=timezone.utc)
    with workspace_database.begin() as session:
        session.add(WorkspaceMember(workspace_id=workspace_tenants.spaces["A"], user_id=workspace_tenants.users["A"], role="viewer"))
        for member in session.scalars(select(WorkspaceMember).where(WorkspaceMember.workspace_id == workspace_tenants.spaces["A"])):
            member.joined_at = timestamp
    ids = []
    for offset in range(4):
        data = member_api("GET", params={"limit": 1, "offset": offset}).json()
        assert data["total"] == 4
        ids.append(data["items"][0]["user_id"])
    assert ids == [str(workspace_tenants.users["A"])] + sorted(str(workspace_tenants.users[key]) for key in "CDE")


@pytest.mark.parametrize("params,status", [({"offset": 2**63-1}, 200), ({"offset": 2**63}, 422), ({"offset": -1}, 422), ({"offset": "abc"}, 422), ({"offset": "9"*1000}, 422), ({"limit": 0}, 422), ({"limit": 101}, 422)])
def test_member_pagination_bounds(member_api, params, status):
    response = member_api("GET", params=params)
    assert response.status_code == status
    if status == 200:
        assert response.json()["items"] == [] and response.json()["total"] == 4


def test_database_pk_and_fk_are_real(workspace_database, workspace_tenants):
    with workspace_database() as session:
        assert session.scalar(text("PRAGMA foreign_keys")) == 1
    with pytest.raises(IntegrityError):
        with workspace_database.begin() as session:
            session.add(WorkspaceMember(workspace_id=workspace_tenants.spaces["A"], user_id=workspace_tenants.users["C"], role="viewer"))
    with pytest.raises(IntegrityError):
        with workspace_database.begin() as session:
            session.add(WorkspaceMember(workspace_id=workspace_tenants.spaces["A"], user_id=uuid4(), role="viewer"))


def test_openapi_registers_four_production_operations_and_public_fields():
    spec = app.openapi()
    for path, methods in [("/api/v1/workspaces/{workspace_id}/members", {"get", "post"}),
                          ("/api/v1/workspaces/{workspace_id}/members/{user_id}", {"patch", "delete"})]:
        assert set(spec["paths"][path]) == methods
        for operation in spec["paths"][path].values():
            assert operation["security"] == [{"HTTPBearer": []}]
    assert set(spec["components"]["schemas"]["MemberResponse"]["properties"]) == FIELDS
    assert "content" not in spec["paths"]["/api/v1/workspaces/{workspace_id}/members/{user_id}"]["delete"]["responses"]["204"]
