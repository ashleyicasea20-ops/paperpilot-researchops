"""Cross-user/Workspace API isolation with real SQLite data and signed JWTs."""

from uuid import uuid4

import jwt
import pytest
from sqlalchemy import select, update

from app.core.token import issue_access_token
from app.models import User, Workspace, WorkspaceMember


@pytest.mark.parametrize("user,role,space", [
    ("A", "owner", "A"), ("B", "owner", "B"), ("C", "admin", "A"),
    ("C", "viewer", "B"), ("D", "editor", "A"), ("E", "viewer", "A"),
])
def test_details_use_workspace_scoped_roles(workspace_api, workspace_tenants, user, role, space):
    response = workspace_api("GET", user=user, workspace=space)
    assert response.status_code == 200
    assert response.json()["effective_role"] == role
    assert response.json()["owner_id"] == str(workspace_tenants.users[space])


@pytest.mark.parametrize("method,user,status", [
    ("PATCH", "A", 200), ("PATCH", "B", 404), ("PATCH", "C", 200),
    ("PATCH", "D", 403), ("PATCH", "E", 403), ("PATCH", "F", 404),
    ("DELETE", "A", 204), ("DELETE", "B", 404), ("DELETE", "C", 403),
    ("DELETE", "D", 403), ("DELETE", "E", 403), ("DELETE", "F", 404),
])
def test_mutation_role_matrix_and_persisted_outcome(workspace_api, workspace_database, workspace_tenants, method, user, status):
    options = {"json": {"name": "Authorized change"}} if method == "PATCH" else {}
    response = workspace_api(method, user=user, workspace="A", **options)
    assert response.status_code == status
    assert response.headers["cache-control"] == "no-store"
    with workspace_database() as session:
        workspace = session.get(Workspace, workspace_tenants.spaces["A"])
        if method == "DELETE" and status == 204:
            assert workspace is None
        else:
            assert workspace.name == ("Authorized change" if status == 200 else "Workspace A")
        assert session.get(Workspace, workspace_tenants.spaces["B"]).name == "Workspace B"


@pytest.mark.parametrize("user,expected", [("A", {"A": "owner"}), ("B", {"B": "owner"}), ("C", {"A": "admin", "B": "viewer"}), ("D", {"A": "editor"}), ("E", {"A": "viewer"}), ("F", {})])
def test_lists_filter_and_count_only_accessible_spaces(workspace_api, workspace_tenants, user, expected):
    response = workspace_api("GET", user=user, params={"user_id": str(workspace_tenants.users["A"]), "role": "owner"})
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == len(expected)
    assert {item["id"]: item["effective_role"] for item in data["items"]} == {
        str(workspace_tenants.spaces[key]): role for key, role in expected.items()
    }


@pytest.mark.parametrize("method", ["GET", "PATCH", "DELETE"])
def test_nonmember_and_missing_are_identical(workspace_api, method):
    options = {"json": {"name": "Attempt"}} if method == "PATCH" else {}
    denied = workspace_api(method, user="B", workspace="A", **options)
    missing = workspace_api(method, user="B", workspace=uuid4(), **options)
    assert denied.status_code == missing.status_code == 404
    assert denied.json() == missing.json() == {"detail": "Workspace not found"}
    assert workspace_api(method, user="A", workspace="B", **options).status_code == 404


@pytest.mark.parametrize("method,item", [("POST", False), ("GET", False), ("GET", True), ("PATCH", True), ("DELETE", True)])
@pytest.mark.parametrize("identity", ["missing", "bad-token", "disabled", "deleted"])
def test_all_crud_require_current_active_identity(workspace_api, workspace_database, workspace_tenants, method, item, identity):
    headers = {"Authorization": "Bearer " + workspace_tenants.tokens["A"]}
    if identity == "missing":
        headers = {}
    elif identity == "bad-token":
        headers = {"Authorization": "Bearer invalid.synthetic.token"}
    elif identity == "disabled":
        with workspace_database.begin() as session:
            session.get(User, workspace_tenants.users["A"]).is_active = False
    elif identity == "deleted":
        # Delete only a non-owner synthetic account and reuse its old token.
        headers = {"Authorization": "Bearer " + workspace_tenants.tokens["F"]}
        with workspace_database.begin() as session:
            session.delete(session.get(User, workspace_tenants.users["F"]))
    options = {"json": {"name": "Private attempt"}} if method in ("POST", "PATCH") else {}
    response = workspace_api(method, workspace="A" if item else None, headers=headers, **options)
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    assert response.headers["cache-control"] == "no-store"
    with workspace_database() as session:
        assert len(list(session.scalars(select(Workspace)))) == 2
        assert session.get(Workspace, workspace_tenants.spaces["A"]).name == "Workspace A"


def test_signed_missing_account_is_rejected(workspace_api, auth_settings):
    token = issue_access_token(uuid4(), auth_settings)
    assert workspace_api("POST", headers={"Authorization": "Bearer " + token}, json={"name": "No account"}).status_code == 401


def test_signed_extra_claims_and_query_fields_cannot_grant_access(workspace_api, workspace_tenants, auth_settings):
    key = bytes.fromhex(auth_settings.jwt_secret_key.get_secret_value())
    claims = jwt.decode(workspace_tenants.tokens["F"], key, algorithms=["HS256"], audience="paperpilot-api", issuer="paperpilot-researchops")
    claims.update(role="owner", owner_id=str(workspace_tenants.users["A"]), workspace_id=str(workspace_tenants.spaces["A"]))
    token = jwt.encode(claims, key, algorithm="HS256")
    response = workspace_api("PATCH", workspace="A", headers={"Authorization": "Bearer " + token},
                             params={"user_id": str(workspace_tenants.users["A"]), "permission": "workspace:read"}, json={"name": "Attempt"})
    assert response.status_code == 404


def test_downgrade_removal_and_other_space_are_independent(workspace_api, workspace_database, workspace_tenants):
    assert workspace_api("PATCH", user="C", workspace="A", json={"name": "Admin update"}).status_code == 200
    assert workspace_api("PATCH", user="C", workspace="B", json={"name": "Cannot update B"}).status_code == 403
    with workspace_database.begin() as session:
        session.get(WorkspaceMember, (workspace_tenants.spaces["A"], workspace_tenants.users["C"])).role = "viewer"
    assert workspace_api("PATCH", user="C", workspace="A", json={"name": "Cannot update A"}).status_code == 403
    with workspace_database.begin() as session:
        session.delete(session.get(WorkspaceMember, (workspace_tenants.spaces["A"], workspace_tenants.users["C"])))
    assert workspace_api("GET", user="C", workspace="A").status_code == 404
    listed = workspace_api("GET", user="C").json()
    assert listed["total"] == 1 and listed["items"][0]["id"] == str(workspace_tenants.spaces["B"])


def test_redundant_membership_does_not_change_owner(workspace_api, workspace_database, workspace_tenants):
    with workspace_database.begin() as session:
        session.add(WorkspaceMember(workspace_id=workspace_tenants.spaces["A"], user_id=workspace_tenants.users["A"], role="viewer"))
    assert workspace_api("GET").json()["items"][0]["effective_role"] == "owner"
    assert workspace_api("PATCH", workspace="A", json={"name": "Still owner"}).status_code == 200
    assert workspace_api("DELETE", workspace="A").status_code == 204


@pytest.mark.parametrize("role", ["owner", "superuser", "ADMIN", ""])
def test_invalid_stored_member_role_is_not_access(workspace_api, workspace_database, workspace_tenants, role):
    # Corrupt only this isolated SQLite DB to test defense beyond the CHECK.
    with workspace_database.kw["bind"].begin() as connection:
        connection.exec_driver_sql("PRAGMA ignore_check_constraints=ON")
        connection.execute(update(WorkspaceMember).where(
            WorkspaceMember.workspace_id == workspace_tenants.spaces["A"], WorkspaceMember.user_id == workspace_tenants.users["C"],
        ).values(role=role))
        connection.exec_driver_sql("PRAGMA ignore_check_constraints=OFF")
    assert workspace_api("GET", user="C", workspace="A").status_code == 404
    assert workspace_api("PATCH", user="C", workspace="A", json={"name": "Attempt"}).status_code == 404
    assert workspace_api("DELETE", user="C", workspace="A").status_code == 404
    assert workspace_api("GET", user="C").json()["total"] == 1
