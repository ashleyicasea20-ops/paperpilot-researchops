"""Mock server identity/lease tests, never native PostgreSQL evidence."""

from copy import deepcopy
from unittest.mock import Mock

import pytest

from tests.postgres import safety
from tests.postgres.settings import PgSafetyError


def test_matching_mock_snapshot(settings, snapshot):
    safety.validate_snapshot(snapshot, settings)


@pytest.mark.parametrize("key,value", [
    ("database_name", "paperpilot_dev"), ("session_user_name", "postgres"),
    ("current_user_name", "paperpilot_test_owner"), ("address", "127.0.0.2"),
    ("port", 5432), ("version", 160012), ("version", 180000), ("version", "170012"),
    ("search_path", '"$user", public'), ("database_owner", "paperpilot_test_app"),
    ("memberships", 1), ("rolsuper", True), ("rolcreatedb", True),
    ("rolcreaterole", True), ("rolreplication", True), ("rolbypassrls", True),
    ("rolcanlogin", False), ("schema_create", True), ("database_create", True),
    ("marker_write", True), ("rolsuper", None), ("database_temp", True),
    ("marker_schema_create", True), ("marker_owner", "other"),
    ("version_owner", "other"), ("version_write", True),
    ("effective_schemas", ["pg_temp_1", "pg_catalog", "public"]),
])
def test_identity_mismatches_discard_connection(settings, opt_in, snapshot,
                                               mock_connection, monkeypatch, key, value):
    snapshot[0][key] = value
    monkeypatch.setattr(safety, "read_snapshot", Mock(return_value=snapshot))
    with pytest.raises(PgSafetyError):
        safety.verify_connection(mock_connection, settings, opt_in)
    mock_connection.rollback.assert_called_once_with()
    mock_connection.invalidate.assert_called_once_with()
    mock_connection.close.assert_called_once_with()
    with pytest.raises(PgSafetyError):
        safety.require_verified(mock_connection)
    assert all(not str(call.args[0]).upper().startswith(("INSERT", "UPDATE", "DELETE", "CREATE"))
               for call in mock_connection.exec_driver_sql.call_args_list)


@pytest.mark.parametrize("kind", ["marker-missing", "marker-wrong", "purpose", "revision-empty",
                                  "revision-extra", "revision-wrong", "catalog", "owner", "privileges", "schema"])
def test_server_attestation_fail_closed(settings, snapshot, kind):
    identity, marker, revisions, catalog, schema = deepcopy(snapshot)
    if kind == "marker-missing":
        marker = {}
    elif kind == "marker-wrong":
        marker["environment_id"] = "different"
    elif kind == "purpose":
        marker["purpose"] = "development"
    elif kind == "revision-empty":
        revisions = ()
    elif kind == "revision-extra":
        revisions = revisions * 2
    elif kind == "revision-wrong":
        revisions = ("other",)
    elif kind in ("catalog", "owner", "privileges"):
        catalog[{"catalog": "catalog_valid", "owner": "tables_owned", "privileges": "dml_only"}[kind]] = False
    else:
        schema["users"]["indexes"] = {}
    with pytest.raises(PgSafetyError):
        safety.validate_snapshot((identity, marker, revisions, catalog, schema), settings)


def test_verification_ends_read_transaction(settings, opt_in, snapshot, mock_connection, monkeypatch):
    monkeypatch.setattr(safety, "read_snapshot", Mock(return_value=snapshot))
    safety.verify_connection(mock_connection, settings, opt_in)
    mock_connection.exec_driver_sql.assert_any_call("SET TRANSACTION READ ONLY")
    mock_connection.rollback.assert_called_once_with()
    mock_connection.close.assert_not_called()
    safety.require_verified(mock_connection)
    mock_connection.connection.driver_connection = object()
    with pytest.raises(PgSafetyError):
        safety.require_verified(mock_connection)


@pytest.mark.parametrize("fault", ["snapshot", "rollback", "active", "dialect"])
def test_verification_faults_never_release_a_verified_lease(
    settings, opt_in, snapshot, mock_connection, monkeypatch, fault,
):
    reader = Mock(return_value=snapshot)
    monkeypatch.setattr(safety, "read_snapshot", reader)
    if fault == "snapshot":
        reader.side_effect = RuntimeError("synthetic-private-marker")
    elif fault == "rollback":
        mock_connection.rollback.side_effect = RuntimeError("synthetic-private-marker")
    elif fault == "active":
        mock_connection.in_transaction.return_value = True
    else:
        mock_connection.dialect.name = "sqlite"
    with pytest.raises(PgSafetyError) as caught:
        safety.verify_connection(mock_connection, settings, opt_in)
    assert "synthetic-private-marker" not in str(caught.value)
    assert caught.value.__context__ is None
    mock_connection.close.assert_called_once_with()
    with pytest.raises(PgSafetyError):
        safety.require_verified(mock_connection)


def test_reviewed_check_normalization_preserves_role_and_blank_semantics():
    assert safety.canonical_sql("role IN ('admin', 'editor', 'viewer')") == safety.canonical_sql(
        "((role)::text = ANY ((ARRAY['admin'::character varying, 'editor'::character varying, 'viewer'::character varying])::text[]))"
    )
    assert safety.canonical_sql("role IN ('Admin','editor','viewer')") != safety.canonical_sql("role IN ('admin','editor','viewer')")
    assert safety.canonical_sql("role IN ('admin','editor','viewer') OR true") != safety.canonical_sql("role IN ('admin','editor','viewer')")
    assert safety.canonical_sql("btrim(email, ' ')") != safety.canonical_sql("btrim(email, '')")
