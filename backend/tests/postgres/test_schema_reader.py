"""Offline PostgreSQL reflection shapes; no server or migration is executed."""

from copy import deepcopy
from unittest.mock import Mock

import pytest

from app.db.base import Base
import app.models  # noqa: F401
from tests.postgres import safety


@pytest.fixture
def inspector(monkeypatch):
    expected = safety.expected_schema()
    inspector = Mock()
    inspector.get_table_names.return_value = ["users", "workspaces", "workspace_members", "alembic_version"]
    inspector.get_columns.side_effect = lambda name, **kw: [
        dict(name=key, type=Base.metadata.tables[name].c[key].type,
             nullable=value[1], default=value[2]) for key, value in expected[name]["columns"].items()
    ]
    inspector.get_pk_constraint.side_effect = lambda name, **kw: dict(
        name=expected[name]["pk"][0], constrained_columns=expected[name]["pk"][1])
    inspector.get_foreign_keys.side_effect = lambda name, **kw: [
        dict(name=key, constrained_columns=value[0], referred_schema=value[1],
             referred_table=value[2], referred_columns=value[3],
             options=dict(ondelete=value[4], onupdate=value[5]))
        for key, value in expected[name]["fks"].items()
    ]
    inspector.get_check_constraints.side_effect = lambda name, **kw: [
        dict(name=key, sqltext=value) for key, value in expected[name]["checks"].items()
    ]
    indexes = {}
    for name, table in expected.items():
        indexes[name] = [dict(name=key, unique=value[0], column_names=list(value[1]),
                              expressions=["lower(email::text)"] if key == "uq_users_email_lower" else list(value[1]),
                              dialect_options={"postgresql_include": []})
                         for key, value in table["indexes"].items()]
    inspector.get_indexes.side_effect = lambda name, **kw: deepcopy(indexes[name])
    inspector.synthetic_indexes = indexes
    monkeypatch.setattr(safety, "inspect", Mock(return_value=inspector))
    return inspector


def test_reflected_schema_matches_reviewed_definitions(inspector, mock_connection):
    assert safety.read_schema(mock_connection) == safety.expected_schema()


@pytest.mark.parametrize("kind", ["partial", "include", "missing-index"])
def test_reflected_unique_index_cannot_be_weakened(inspector, mock_connection, kind):
    index = inspector.synthetic_indexes["users"][0]
    if kind == "partial":
        index["dialect_options"]["postgresql_where"] = "is_active"
    elif kind == "include":
        index["dialect_options"]["postgresql_include"] = ["password_hash"]
    else:
        inspector.synthetic_indexes["users"].clear()
    assert safety.read_schema(mock_connection) != safety.expected_schema()


def test_extra_public_tables_are_rejected(inspector, mock_connection):
    inspector.get_table_names.return_value += ["unapproved_table"]
    with pytest.raises(Exception, match="Unexpected test schema"):
        safety.read_schema(mock_connection)


def test_snapshot_reader_only_issues_read_queries(snapshot, mock_connection, monkeypatch):
    identity, marker, revisions, catalog, schema = snapshot
    results = [Mock() for _ in range(4)]
    for result, value in zip((results[0], results[1], results[3]), (identity, marker, catalog)):
        result.mappings.return_value.one.return_value = value
    results[2].scalars.return_value = revisions
    mock_connection.exec_driver_sql.side_effect = results
    monkeypatch.setattr(safety, "read_schema", Mock(return_value=schema))
    assert safety.read_snapshot(mock_connection) == snapshot
    assert all(call.args[0].lstrip().upper().startswith("SELECT")
               for call in mock_connection.exec_driver_sql.call_args_list)


def test_generated_columns_are_not_the_reviewed_schema(inspector, mock_connection):
    columns = inspector.get_columns("users")
    columns[0]["computed"] = {"sqltext": "unapproved_expression"}
    inspector.get_columns.side_effect = None
    inspector.get_columns.return_value = columns
    with pytest.raises(Exception, match="Unexpected generated test column"):
        safety.read_schema(mock_connection)
