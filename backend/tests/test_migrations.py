"""Migration structure and PostgreSQL SQL compilation, without database I/O."""

import ast
from contextlib import nullcontext
from io import StringIO
from pathlib import Path
import runpy
import shutil

from alembic import command
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory
from alembic.util import CommandError
import pytest
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from app.db.base import Base
import app.models  # noqa: F401

BACKEND = Path(__file__).resolve().parents[1]
REVISION = "20261120_0001"
DIALECT = postgresql.dialect()


@pytest.fixture(autouse=True)
def forbid_real_connections(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Migration unit tests must never connect to a database")

    monkeypatch.setattr(sa.engine.Engine, "connect", forbidden)
    monkeypatch.setattr("psycopg.connect", forbidden)


@pytest.fixture
def config():
    return Config(str(BACKEND / "alembic.ini"), output_buffer=StringIO())


@pytest.fixture
def revision(config):
    return ScriptDirectory.from_config(config).get_revision(REVISION).module


@pytest.fixture
def recorded_upgrade(config, revision, monkeypatch):
    """Capture real Alembic operations while compiling them in offline mode."""
    ctx = MigrationContext.configure(dialect_name="postgresql", opts={
        "as_sql": True, "output_buffer": config.output_buffer,
        "target_metadata": Base.metadata, "literal_binds": True,
    })
    operations = Operations(ctx)
    tables, indexes, order = {}, {}, []
    create_table, create_index = operations.create_table, operations.create_index

    def record_table(name, *args, **kwargs):
        table = create_table(name, *args, **kwargs)
        tables[name] = table
        order.append(name)
        return table

    def record_index(name, table_name, columns, **kwargs):
        indexes[str(name)] = (table_name, columns, kwargs.get("unique", False))
        return create_index(name, table_name, columns, **kwargs)

    monkeypatch.setattr(operations, "create_table", record_table)
    monkeypatch.setattr(operations, "create_index", record_index)
    monkeypatch.setattr(revision, "op", operations)
    revision.upgrade()
    return tables, indexes, order


def compile_expression(expression):
    if isinstance(expression, str):
        return expression
    return str(expression.compile(dialect=DIALECT, compile_kwargs={
        "literal_binds": True, "include_table": False,
    }))


def constraint_signature(constraint):
    if isinstance(constraint, sa.CheckConstraint):
        details = compile_expression(constraint.sqltext)
    elif isinstance(constraint, sa.ForeignKeyConstraint):
        details = (tuple(constraint.column_keys),
                   tuple(element.target_fullname for element in constraint.elements),
                   constraint.ondelete, constraint.onupdate)
    else:
        details = tuple(constraint.columns.keys())
    return type(constraint).__name__, str(constraint.name), details


def test_config_and_single_initial_revision(config):
    assert config.get_main_option("sqlalchemy.url") is None
    scripts = ScriptDirectory.from_config(config)
    assert Path(scripts.dir).resolve() == BACKEND / "alembic"
    assert scripts.get_heads() == [REVISION]
    assert scripts.get_bases() == [REVISION]
    revisions = list(scripts.walk_revisions())
    assert len(revisions) == 1
    assert revisions[0].down_revision is None
    assert revisions[0].module.revision == REVISION
    assert callable(revisions[0].module.upgrade)
    assert callable(revisions[0].module.downgrade)


def test_revision_is_independent_of_application_models(config):
    path = ScriptDirectory.from_config(config).get_revision(REVISION).path
    tree = ast.parse(Path(path).read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            assert not (node.module or "").startswith("app")
        elif isinstance(node, ast.Import):
            assert all(not alias.name.startswith("app") for alias in node.names)


def test_revision_template_generates_loadable_history(tmp_path):
    # Exercise the real template in a disposable directory, never project history.
    (tmp_path / "versions").mkdir()
    shutil.copyfile(BACKEND / "alembic" / "script.py.mako", tmp_path / "script.py.mako")
    scripts = ScriptDirectory(str(tmp_path))
    generated = scripts.generate_revision("template_test", "template smoke", refresh=True)
    assert generated.module.revision == "template_test"
    assert generated.module.down_revision is None
    assert callable(generated.module.upgrade)
    assert callable(generated.module.downgrade)


def test_tables_and_foreign_key_creation_order(recorded_upgrade):
    tables, _, order = recorded_upgrade
    assert order == ["users", "workspaces", "workspace_members"]
    assert set(tables) == set(Base.metadata.tables)
    for table in tables.values():
        for fk in table.foreign_keys:
            assert order.index(fk.target_fullname.split(".")[0]) < order.index(table.name)


@pytest.mark.parametrize("name", ["users", "workspaces", "workspace_members"])
def test_migration_column_types_nullability_and_server_defaults_match_orm(recorded_upgrade, name):
    tables, _, _ = recorded_upgrade
    actual, expected = tables[name], Base.metadata.tables[name]
    assert set(actual.c.keys()) == set(expected.c.keys())
    for column in expected.c:
        migrated = actual.c[column.name]
        assert migrated.type.compile(dialect=DIALECT) == column.type.compile(dialect=DIALECT)
        assert migrated.nullable == column.nullable
        if isinstance(column.type, sa.DateTime):
            assert migrated.type.timezone == column.type.timezone
        if isinstance(column.type, sa.Uuid):
            assert migrated.type.as_uuid == column.type.as_uuid
        default = lambda c: None if c.server_default is None else compile_expression(c.server_default.arg)
        assert default(migrated) == default(column)
        # SQLAlchemy client defaults/onupdate must not become server defaults.
        if isinstance(column.type, sa.Uuid):
            assert migrated.server_default is None


@pytest.mark.parametrize("name", ["users", "workspaces", "workspace_members"])
def test_all_named_constraints_match_orm(recorded_upgrade, name):
    tables, _, _ = recorded_upgrade
    assert {constraint_signature(c) for c in tables[name].constraints} == {
        constraint_signature(c) for c in Base.metadata.tables[name].constraints
    }


def test_indexes_match_orm_including_unique_email_expression(recorded_upgrade):
    _, indexes, _ = recorded_upgrade
    expected = {}
    for table in Base.metadata.tables.values():
        for index in table.indexes:
            expected[index.name] = (table.name,
                [compile_expression(e) for e in index.expressions], index.unique)
    actual = {name: (table, [compile_expression(c) for c in cols], unique)
              for name, (table, cols, unique) in indexes.items()}
    assert actual == expected
    assert actual["uq_users_email_lower"] == ("users", ["lower(email)"], True)


def test_offline_upgrade_without_settings_credentials_or_engine(config, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Offline SQL must not load settings or construct engines")

    monkeypatch.setenv("DB_PASSWORD", "offline-secret-sentinel")
    monkeypatch.setenv("DB_PORT", "deliberately-invalid")
    monkeypatch.setattr("app.core.config.Settings.__init__", forbidden)
    monkeypatch.setattr("app.db.session.get_engine", forbidden)
    monkeypatch.setattr(sa, "create_engine", forbidden)
    command.upgrade(config, "head", sql=True)
    sql = config.output_buffer.getvalue()
    assert "offline-secret-sentinel" not in sql
    assert "postgresql://" not in sql
    assert sql.startswith("BEGIN;")
    assert sql.rstrip().endswith("COMMIT;")
    for table in ("users", "workspaces", "workspace_members", "alembic_version"):
        assert f"CREATE TABLE {table}" in sql
    assert f"INSERT INTO alembic_version (version_num) VALUES ('{REVISION}')" in sql
    assert "CREATE UNIQUE INDEX uq_users_email_lower ON users (lower(email))" in sql
    assert "ON DELETE RESTRICT" in sql and sql.count("ON DELETE CASCADE") == 2
    assert "DEFAULT 'viewer'" in sql and "DEFAULT true" in sql


def test_offline_downgrade_drops_in_reverse_dependency_order(config):
    # Offline rendering only; this never executes a DROP against any database.
    command.downgrade(config, f"{REVISION}:base", sql=True)
    sql = config.output_buffer.getvalue()
    order = [sql.index(f"DROP TABLE {name};") for name in
             ("workspace_members", "workspaces", "users")]
    assert order == sorted(order)


@pytest.mark.parametrize("source", ["driver", "settings"])
def test_online_failure_reporting_suppresses_secrets(config, monkeypatch, source):
    from pydantic import ValidationError

    marker = "migration-private-sentinel"
    failure = (sa.exc.OperationalError(None, {"password": marker}, RuntimeError(marker))
               if source == "driver" else ValidationError.from_exception_data(
                   "Settings", [{"type": "int_parsing", "loc": ("db_port",), "input": marker}]))

    def unavailable():
        raise failure

    monkeypatch.setattr("app.db.session.get_engine", unavailable)
    # current reads version state; the mocked engine fails before any connection.
    with pytest.raises(CommandError) as caught:
        command.current(config)
    assert marker not in str(caught.value)
    assert type(failure).__name__ in str(caught.value)
    assert caught.value.__context__ is None


@pytest.mark.parametrize("supplied_connection", [False, True])
def test_online_configuration_uses_shared_engine_or_supplied_connection(config, monkeypatch, supplied_connection):
    from alembic import context

    connection = object()
    configured, engine_calls, runs = [], [], []

    class FakeEngine:
        def connect(self):
            return nullcontext(connection)

    def engine():
        engine_calls.append(True)
        return FakeEngine()

    if supplied_connection:
        config.attributes["connection"] = connection
    monkeypatch.setattr("app.db.session.get_engine", engine)
    monkeypatch.setattr(context, "config", config, raising=False)
    monkeypatch.setattr(context, "is_offline_mode", lambda: False)
    monkeypatch.setattr(context, "configure", lambda **kw: configured.append(kw))
    monkeypatch.setattr(context, "begin_transaction", nullcontext)
    monkeypatch.setattr(context, "run_migrations", lambda: runs.append(True))
    runpy.run_path(str(BACKEND / "alembic" / "env.py"))
    assert configured == [{"connection": connection, "target_metadata": Base.metadata, "compare_type": True}]
    assert len(engine_calls) == (0 if supplied_connection else 1)
    assert runs == [True]
