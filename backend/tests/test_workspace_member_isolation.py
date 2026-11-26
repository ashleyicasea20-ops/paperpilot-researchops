"""Six-user, two-Workspace privacy, revocation and corrupt-role checks."""

from unittest.mock import Mock
from uuid import uuid4

import jwt
import pytest
from app.models import User, WorkspaceMember
from app.services import workspace_members as service


@pytest.mark.parametrize("method", ["GET", "POST", "PATCH", "DELETE"])
def test_other_workspace_and_missing_workspace_have_same_404(member_api, workspace_tenants, method):
    options = {"json": {"user_id": str(workspace_tenants.users["F"]), "role": "viewer"}} if method == "POST" else {"json": {"role": "viewer"}} if method == "PATCH" else {}
    a = member_api(method, user="A", workspace="B", target="C" if method in ("PATCH", "DELETE") else None, **options)
    b = member_api(method, user="A", workspace=uuid4(), target="C" if method in ("PATCH", "DELETE") else None, **options)
    assert a.status_code == b.status_code == 404
    assert a.json() == b.json() == {"detail": "Workspace not found"}


@pytest.mark.parametrize("actor", ["C", "D", "E", "F"])
@pytest.mark.parametrize("target_exists", [True, False])
def test_unauthorized_post_cannot_enumerate_users(member_api, workspace_tenants, monkeypatch, actor, target_exists):
    # Admin is intentionally requesting admin, which exceeds its authority.
    spy = Mock(side_effect=AssertionError("Unauthorized target lookup"))
    monkeypatch.setattr(service, "_active_target_exists", spy)
    locks = Mock(side_effect=AssertionError("Unauthorized target locks"))
    monkeypatch.setattr(service, "lock_workspace_permission", locks)
    response = member_api("POST", user=actor, json={"user_id": str(workspace_tenants.users["F"] if target_exists else uuid4()), "role": "admin"})
    assert response.status_code == (404 if actor == "F" else 403)
    spy.assert_not_called()
    locks.assert_not_called()


@pytest.mark.parametrize("method", ["GET", "POST", "PATCH", "DELETE"])
@pytest.mark.parametrize("identity", ["missing", "bad", "disabled", "deleted", "duplicate"])
def test_all_member_routes_require_current_unambiguous_identity(member_api, workspace_database, workspace_tenants, method, identity):
    token = workspace_tenants.tokens["A"]
    headers = {"Authorization": "Bearer " + token}
    if identity == "missing":
        headers = {}
    elif identity == "bad":
        headers = {"Authorization": "Bearer invalid-token"}
    elif identity == "disabled":
        with workspace_database.begin() as session:
            session.get(User, workspace_tenants.users["A"]).is_active = False
    elif identity == "deleted":
        with workspace_database.begin() as session:
            session.delete(session.get(User, workspace_tenants.users["F"]))
        headers = {"Authorization": "Bearer " + workspace_tenants.tokens["F"]}
    else:
        headers = [("Authorization", "Bearer " + token), ("Authorization", "Bearer " + token)]
    options = {"json": {"user_id": str(uuid4()), "role": "viewer"}} if method == "POST" else {"json": {"role": "viewer"}} if method == "PATCH" else {}
    response = member_api(method, target="E" if method in ("PATCH", "DELETE") else None, headers=headers, **options)
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    assert response.headers["cache-control"] == "no-store"


def test_remove_and_downgrade_take_effect_without_changing_other_workspace(member_api, workspace_api, workspace_tenants):
    assert member_api("PATCH", target="C", json={"role": "viewer"}).status_code == 200
    assert workspace_api("PATCH", user="C", workspace="A", json={"name": "Denied"}).status_code == 403
    assert member_api("POST", user="C", json={"user_id": str(workspace_tenants.users["F"]), "role": "viewer"}).status_code == 403
    assert member_api("DELETE", target="C").status_code == 204
    assert member_api("GET", user="C").status_code == 404
    assert workspace_api("GET", user="C", workspace="A").status_code == 404
    assert member_api("GET", user="C", workspace="B").status_code == 200
    assert workspace_api("GET", user="C", workspace="B").json()["effective_role"] == "viewer"


def test_disabled_target_rejected_on_add_but_existing_members_can_be_removed(member_api, workspace_database, workspace_tenants):
    with workspace_database.begin() as session:
        session.get(User, workspace_tenants.users["F"]).is_active = False
        session.get(User, workspace_tenants.users["E"]).is_active = False
    response = member_api("POST", json={"user_id": str(workspace_tenants.users["F"]), "role": "viewer"})
    assert response.status_code == 404 and response.json() == {"detail": "Member not found"}
    assert member_api("DELETE", target="E").status_code == 204


@pytest.mark.parametrize("role", ["owner", "ADMIN", "superuser", ""])
def test_invalid_stored_role_is_excluded_and_cannot_be_managed(member_api, workspace_database, workspace_tenants, role):
    with workspace_database.kw["bind"].begin() as connection:
        connection.exec_driver_sql("PRAGMA ignore_check_constraints=ON")
        connection.execute(WorkspaceMember.__table__.update().where(
            WorkspaceMember.workspace_id == workspace_tenants.spaces["A"], WorkspaceMember.user_id == workspace_tenants.users["D"],
        ).values(role=role))
        connection.exec_driver_sql("PRAGMA ignore_check_constraints=OFF")
    data = member_api("GET").json()
    assert data["total"] == 3 and str(workspace_tenants.users["D"]) not in {row["user_id"] for row in data["items"]}
    assert member_api("GET", user="D").status_code == 404
    assert member_api("PATCH", target="D", json={"role": "viewer"}).status_code == 403
    assert member_api("DELETE", target="D").status_code == 403


def test_forged_jwt_claims_and_body_actor_cannot_grant_management(member_api, workspace_tenants, auth_settings):
    key = bytes.fromhex(auth_settings.jwt_secret_key.get_secret_value())
    claims = jwt.decode(workspace_tenants.tokens["E"], key, algorithms=["HS256"], audience="paperpilot-api", issuer="paperpilot-researchops")
    claims.update(role="owner", owner_id=str(workspace_tenants.users["A"]))
    token = jwt.encode(claims, key, algorithm="HS256")
    response = member_api("PATCH", target="D", json={"role": "admin"}, headers={"Authorization": "Bearer " + token})
    assert response.status_code == 403
    assert member_api("PATCH", target="D", json={"role": "admin", "user_id": str(workspace_tenants.users["A"])}).status_code == 422


@pytest.mark.parametrize("method,target", [("GET", None), ("POST", None), ("PATCH", "bad-uuid"), ("DELETE", "bad-uuid")])
def test_bad_uuid_is_redacted(member_api, method, target):
    options = {"json": {"user_id": "bad-uuid", "role": "viewer"}} if method == "POST" else {"json": {"role": "viewer"}} if method == "PATCH" else {}
    response = member_api(method, workspace="bad-uuid" if method == "GET" else "A", target=target, **options)
    assert response.status_code == 422 and response.json() == {"detail": "Invalid member input"}


@pytest.mark.parametrize("target", [None, "E"])
def test_method_not_allowed_is_nostore(member_api, target):
    response = member_api("PUT", target=target)
    assert response.status_code == 405 and response.headers["allow"]
    assert response.headers["cache-control"] == "no-store"
