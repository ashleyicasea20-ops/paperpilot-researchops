"""Production Workspace API contracts, strict inputs, pagination and cascades."""

from datetime import datetime, timezone
from unittest.mock import Mock
from uuid import UUID, uuid4

import pytest
from sqlalchemy import event, func, select, text

from app.db import session as db
from app.main import app
from app.models import User, Workspace, WorkspaceMember
from app.schemas.workspace import WorkspaceListResponse

PUBLIC_FIELDS = {"id", "name", "owner_id", "created_at", "updated_at", "effective_role"}


def test_create_persists_owner_and_public_fields_without_owner_membership(workspace_api, workspace_database, workspace_tenants):
    response = workspace_api("POST", json={"name": "Solar Forecasting Research"})
    assert response.status_code == 201
    assert response.headers["cache-control"] == "no-store"
    data = response.json()
    assert set(data) == PUBLIC_FIELDS
    assert data["owner_id"] == str(workspace_tenants.users["A"])
    assert data["effective_role"] == "owner"
    assert UUID(data["id"]).version == 4
    assert datetime.fromisoformat(data["created_at"])
    assert datetime.fromisoformat(data["updated_at"])
    with workspace_database() as session:
        persisted = session.get(Workspace, UUID(data["id"]))
        assert persisted.name == data["name"]
        assert persisted.owner_id == workspace_tenants.users["A"]
        assert session.scalar(select(func.count()).select_from(WorkspaceMember).where(WorkspaceMember.workspace_id == persisted.id)) == 0


@pytest.mark.parametrize("method", ["POST", "PATCH"])
@pytest.mark.parametrize("value", [None, True, 123, [], {}, "", " \t\r\n", "\u00a0\u3000\u2003", "\x1c\x1f", "a" * 151, "x\x00y", "\ud800"])
def test_invalid_names_are_rejected_without_echo(workspace_api, workspace_database, workspace_tenants, method, value):
    # JSON unicode escapes allow malformed Unicode to reach input validation.
    import json
    response = workspace_api(method, workspace="A" if method == "PATCH" else None,
                             content=json.dumps({"name": value}), headers={
                                 "Authorization": "Bearer " + workspace_tenants.tokens["A"],
                                 "Content-Type": "application/json",
                             })
    assert response.status_code == 422
    assert response.json() == {"detail": "Invalid workspace input"}
    assert response.headers["cache-control"] == "no-store"
    with workspace_database() as session:
        assert session.scalar(select(func.count()).select_from(Workspace)) == 2
        assert session.get(Workspace, workspace_tenants.spaces["A"]).name == "Workspace A"


@pytest.mark.parametrize("method", ["POST", "PATCH"])
@pytest.mark.parametrize("field", ["owner_id", "effective_role", "role", "user_id", "id", "memberships", "created_at", "updated_at"])
def test_extra_write_fields_cannot_override_internal_state(workspace_api, workspace_database, workspace_tenants, method, field):
    response = workspace_api(method, workspace="A" if method == "PATCH" else None,
                             json={"name": "Valid", field: str(uuid4())})
    assert response.status_code == 422
    with workspace_database() as session:
        assert session.get(Workspace, workspace_tenants.spaces["A"]).owner_id == workspace_tenants.users["A"]
        assert session.scalar(select(func.count()).select_from(Workspace)) == 2


@pytest.mark.parametrize("name", ["研究空间", "a" * 150, "  Research  ", "\u3000X\u3000"])
def test_valid_names_preserve_input(workspace_api, name):
    response = workspace_api("POST", json={"name": name})
    assert response.status_code == 201
    assert response.json()["name"] == name


@pytest.mark.parametrize("method", ["POST", "PATCH"])
@pytest.mark.parametrize("body", [{}, [], None])
def test_missing_name_or_wrong_body_is_rejected(workspace_api, method, body):
    assert workspace_api(method, workspace="A" if method == "PATCH" else None, json=body).status_code == 422


@pytest.mark.parametrize("body", [b'{"name":', b'\xff'])
def test_malformed_json_is_redacted(workspace_api, workspace_tenants, body):
    response = workspace_api("POST", content=body, headers={
        "Authorization": "Bearer " + workspace_tenants.tokens["A"], "Content-Type": "application/json",
    })
    assert response.status_code == 422
    assert response.json() == {"detail": "Invalid workspace input"}


def test_patch_returns_persisted_values_and_updated_timestamp(workspace_api, workspace_database, workspace_tenants):
    old = datetime(2001, 1, 1, tzinfo=timezone.utc)
    with workspace_database.begin() as session:
        session.get(Workspace, workspace_tenants.spaces["A"]).updated_at = old
    response = workspace_api("PATCH", user="C", workspace="A", json={"name": "Updated research"})
    assert response.status_code == 200
    assert set(response.json()) == PUBLIC_FIELDS
    assert response.json()["effective_role"] == "admin"
    assert datetime.fromisoformat(response.json()["updated_at"]).replace(tzinfo=timezone.utc) > old
    assert workspace_api("GET", workspace="A").json()["name"] == "Updated research"
    with workspace_database() as session:
        assert session.get(Workspace, workspace_tenants.spaces["A"]).name == "Updated research"


def test_delete_cascades_memberships_but_keeps_users_and_other_space(workspace_api, workspace_database, workspace_tenants):
    response = workspace_api("DELETE", workspace="A")
    assert response.status_code == 204
    assert response.content == b""
    assert response.headers["cache-control"] == "no-store"
    with workspace_database() as session:
        assert session.scalar(text("PRAGMA foreign_keys")) == 1
        assert session.get(Workspace, workspace_tenants.spaces["A"]) is None
        assert session.get(Workspace, workspace_tenants.spaces["B"]) is not None
        assert session.scalar(select(func.count()).select_from(User)) == 6
        assert session.scalar(select(func.count()).select_from(WorkspaceMember).where(WorkspaceMember.workspace_id == workspace_tenants.spaces["A"])) == 0
        assert session.scalar(select(func.count()).select_from(WorkspaceMember)) == 1


def test_pagination_is_scoped_stable_and_deduplicates_owner(workspace_api, workspace_database, workspace_tenants):
    timestamp = datetime(2001, 1, 1, tzinfo=timezone.utc)
    with workspace_database.begin() as session:
        session.add(WorkspaceMember(workspace_id=workspace_tenants.spaces["A"], user_id=workspace_tenants.users["A"], role="viewer"))
        for _ in range(5):
            session.add(Workspace(owner_id=workspace_tenants.users["A"], name="Same name", created_at=timestamp))
    with workspace_database() as session:
        expected = list(session.scalars(select(Workspace.id).where(Workspace.owner_id == workspace_tenants.users["A"]).order_by(Workspace.created_at, Workspace.id)))
    ids = []
    for offset in [0, 2, 4]:
        data = workspace_api("GET", params={"limit": 2, "offset": offset}).json()
        assert data["total"] == 6 and data["limit"] == 2 and data["offset"] == offset
        assert all(item["effective_role"] == "owner" for item in data["items"])
        ids.extend(UUID(item["id"]) for item in data["items"])
    assert ids == expected and len(set(ids)) == 6
    assert workspace_tenants.spaces["B"] not in ids
    assert workspace_api("GET", params={"offset": 100}).json()["items"] == []
    assert workspace_api("GET", params={"offset": 100}).json()["total"] == 6


@pytest.mark.parametrize("params", [{"limit": 0}, {"limit": -1}, {"limit": 101}, {"limit": "1.5"}, {"offset": -1}, {"offset": "x"}])
def test_pagination_limits_are_validated(workspace_api, params):
    assert workspace_api("GET", params=params).status_code == 422


@pytest.mark.parametrize("offset", [0, 1, 2**63 - 1])
def test_offset_supported_boundaries(workspace_api, workspace_tenants, offset):
    response = workspace_api("GET", params={"offset": str(offset)})
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 1 and data["limit"] == 20 and data["offset"] == offset
    assert [row["id"] for row in data["items"]] == ([str(workspace_tenants.spaces["A"])] if offset == 0 else [])


@pytest.mark.parametrize(
    "offset", [str(2**63), str(2**63 + 1), "-1", "1.5", "x", "9" * 1000],
    ids=["int64-overflow", "int64-overflow-plus-one", "negative", "fraction", "text", "oversized-digits"],
)
def test_offset_invalid_input_is_rejected_before_database(
    workspace_api, workspace_database, monkeypatch, offset,
):
    engine = workspace_database.kw["bind"]
    statements = []
    factory = Mock(wraps=db.get_session_factory)

    def observe(connection, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", observe)
    try:
        with monkeypatch.context() as patch:
            patch.setattr(db, "get_session_factory", factory)
            response = workspace_api("GET", params={"offset": offset})
    finally:
        event.remove(engine, "before_cursor_execute", observe)
    assert response.status_code == 422
    assert response.json() == {"detail": "Invalid workspace input"}
    assert response.headers["cache-control"] == "no-store"
    factory.assert_not_called()
    assert statements == []


@pytest.mark.parametrize("method", ["PATCH", "DELETE"])
def test_cors_preserves_origin_allowlist(auth_client, method):
    headers = {"Origin": "http://localhost:3000", "Access-Control-Request-Method": method}
    response = auth_client.options("/api/v1/workspaces/" + str(uuid4()), headers=headers)
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:3000"
    assert method in response.headers["access-control-allow-methods"]
    headers["Origin"] = "https://untrusted.example"
    denied = auth_client.options("/api/v1/workspaces", headers=headers)
    assert denied.status_code == 400
    assert "access-control-allow-origin" not in denied.headers


@pytest.mark.parametrize("path,method", [("/api/v1/auth/me", "POST"), ("/api/v1/workspaces", "PUT"), ("/api/v1/workspaces/" + str(uuid4()), "PUT")])
def test_protected_405_preserves_allow_and_no_store(auth_client, path, method):
    response = getattr(auth_client, method.lower())(path)
    assert response.status_code == 405
    assert response.headers["allow"]
    assert response.headers["cache-control"] == "no-store"


def test_shared_auth_fixture_enforces_foreign_keys(auth_database):
    with auth_database() as session:
        assert session.scalar(text("PRAGMA foreign_keys")) == 1


def test_openapi_exposes_five_real_crud_operations_and_public_schemas():
    spec = app.openapi()
    assert set(spec["paths"]["/api/v1/workspaces"]) == {"get", "post"}
    assert set(spec["paths"]["/api/v1/workspaces/{workspace_id}"]) == {"get", "patch", "delete"}
    for path in ["/api/v1/workspaces", "/api/v1/workspaces/{workspace_id}"]:
        for operation in spec["paths"][path].values():
            assert operation["security"] == [{"HTTPBearer": []}]
    schemas = spec["components"]["schemas"]
    offset = next(parameter for parameter in spec["paths"]["/api/v1/workspaces"]["get"]["parameters"] if parameter["name"] == "offset")
    assert offset["schema"]["minimum"] == 0
    assert offset["schema"]["maximum"] == 2**63 - 1
    # FastAPI's OpenAPI Schema model stores maximum as float; check the exact
    # response-model bound before that representation can round an int64 limit.
    assert WorkspaceListResponse.model_json_schema()["properties"]["offset"]["maximum"] == 2**63 - 1
    for name in ["WorkspaceCreateRequest", "WorkspaceUpdateRequest"]:
        assert set(schemas[name]["properties"]) == {"name"}
        assert schemas[name]["additionalProperties"] is False
    assert set(schemas["WorkspaceResponse"]["properties"]) == PUBLIC_FIELDS
    assert "content" not in spec["paths"]["/api/v1/workspaces/{workspace_id}"]["delete"]["responses"]["204"]
