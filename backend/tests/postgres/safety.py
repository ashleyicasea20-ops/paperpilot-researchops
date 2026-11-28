"""Read-only server attestation. Mock snapshots are used only by offline tests."""

from dataclasses import dataclass
from weakref import WeakKeyDictionary, ref
from contextvars import ContextVar
import re

from sqlalchemy import inspect
from sqlalchemy.dialects import postgresql

from tests.postgres.settings import PgSafetyError, preflight

REVISION = "20261120_0001"
OWNER = "paperpilot_test_owner"
PURPOSE = "paperpilot-postgres-tests"
_TICKET = "paperpilot.pg.verified_lease"
_verifying = ContextVar("paperpilot_pg_attestation", default=None)


def canonical_sql(value):
    if value is None:
        return None
    # Only normalize the small, reviewed schema expressions. Preserve every SQL
    # string literal, including the Unicode whitespace set and role spelling.
    parts = re.split(r"('(?:''|[^'])*')", str(value))
    for i in range(0, len(parts), 2):
        part = re.sub(r"::(?:character varying|text)(?:\[\])?", "", parts[i], flags=re.I)
        parts[i] = re.sub(r"[\s()]", "", part).lower()
    result = "".join(parts)
    return result.replace("rolein'admin','editor','viewer'", "role=anyarray['admin','editor','viewer']")


def expected_schema():
    from sqlalchemy import CheckConstraint, ForeignKeyConstraint
    from app.db.base import Base
    import app.models  # noqa: F401
    dialect = postgresql.dialect()
    def expression(value):
        return canonical_sql(value.compile(dialect=dialect, compile_kwargs={"literal_binds": True, "include_table": False}))
    result = {}
    for name, table in Base.metadata.tables.items():
        result[name] = {
            "columns": {c.name: (c.type.compile(dialect=dialect), c.nullable,
                                 None if c.server_default is None else expression(c.server_default.arg))
                        for c in table.columns},
            "pk": (str(table.primary_key.name), tuple(c.name for c in table.primary_key.columns)),
            "fks": {str(c.name): (tuple(c.column_keys), "public",
                                     next(iter(c.elements)).column.table.name,
                                     tuple(e.column.name for e in c.elements), c.ondelete, c.onupdate)
                    for c in table.constraints if isinstance(c, ForeignKeyConstraint)},
            "checks": {str(c.name): canonical_sql(c.sqltext)
                       for c in table.constraints if isinstance(c, CheckConstraint)},
            "indexes": {str(i.name): (bool(i.unique), tuple(expression(e) for e in i.expressions), None, ())
                        for i in table.indexes},
        }
    return result


def read_schema(connection):
    inspector = inspect(connection)
    if set(inspector.get_table_names(schema="public")) != {
        "users", "workspaces", "workspace_members", "alembic_version"
    }:
        raise PgSafetyError("Unexpected test schema")
    dialect = postgresql.dialect()
    result = {}
    for name in ("users", "workspaces", "workspace_members"):
        columns = inspector.get_columns(name, schema="public")
        if any(c.get("computed") or c.get("identity") for c in columns):
            raise PgSafetyError("Unexpected generated test column")
        pk = inspector.get_pk_constraint(name, schema="public")
        result[name] = {
            "columns": {c["name"]: (c["type"].compile(dialect=dialect), c["nullable"], canonical_sql(c["default"]))
                        for c in columns},
            "pk": (pk["name"], tuple(pk["constrained_columns"])),
            "fks": {f["name"]: (tuple(f["constrained_columns"]), f["referred_schema"] or "public",
                                     f["referred_table"], tuple(f["referred_columns"]),
                                     f.get("options", {}).get("ondelete"), f.get("options", {}).get("onupdate"))
                    for f in inspector.get_foreign_keys(name, schema="public")},
            "checks": {c["name"]: canonical_sql(c["sqltext"])
                       for c in inspector.get_check_constraints(name, schema="public")},
            "indexes": {i["name"]: (bool(i["unique"]), tuple(canonical_sql(e) for e in
                                          i.get("expressions", i["column_names"])),
                                          canonical_sql(i.get("dialect_options", {}).get("postgresql_where")),
                                          tuple(i.get("dialect_options", {}).get("postgresql_include", i.get("include_columns", ()))))
                        for i in inspector.get_indexes(name, schema="public")},
        }
    return result


IDENTITY_SQL = """
SELECT current_database() AS database_name, session_user AS session_user_name,
       current_user AS current_user_name, inet_server_addr()::text AS address,
       inet_server_port() AS port, current_setting('server_version_num')::int AS version,
       current_setting('search_path') AS search_path,
       pg_get_userbyid(d.datdba) AS database_owner,
       (SELECT pg_get_userbyid(relowner) FROM pg_class WHERE oid='test_control.environment'::regclass) AS marker_owner,
       (SELECT pg_get_userbyid(relowner) FROM pg_class WHERE oid='public.alembic_version'::regclass) AS version_owner,
       has_table_privilege(current_user, 'public.alembic_version', 'INSERT,UPDATE,DELETE,TRUNCATE') AS version_write,
       current_schemas(true) AS effective_schemas,
       r.rolsuper, r.rolcreatedb, r.rolcreaterole, r.rolreplication, r.rolbypassrls, r.rolcanlogin,
       (SELECT count(*) FROM pg_auth_members WHERE member=r.oid) AS memberships,
       has_schema_privilege(current_user, 'public', 'CREATE') AS schema_create,
       has_database_privilege(current_user, current_database(), 'CREATE') AS database_create,
       has_database_privilege(current_user, current_database(), 'TEMP') AS database_temp,
       has_schema_privilege(current_user, 'test_control', 'CREATE') AS marker_schema_create,
       has_table_privilege(current_user, 'test_control.environment',
                           'INSERT,UPDATE,DELETE,TRUNCATE') AS marker_write
FROM pg_database d JOIN pg_roles r ON r.rolname=current_user
WHERE d.datname=current_database()
"""

CATALOG_SQL = """
SELECT
 NOT EXISTS (
   SELECT 1 FROM pg_constraint c JOIN pg_class t ON t.oid=c.conrelid
   JOIN pg_namespace n ON n.oid=t.relnamespace
   WHERE n.nspname='public' AND t.relname IN ('users','workspaces','workspace_members')
     AND (NOT c.convalidated OR c.condeferrable OR c.condeferred)
 ) AND NOT EXISTS (
   SELECT 1 FROM pg_index i JOIN pg_class t ON t.oid=i.indrelid
   JOIN pg_namespace n ON n.oid=t.relnamespace
   WHERE n.nspname='public' AND t.relname IN ('users','workspaces','workspace_members')
     AND (NOT i.indisvalid OR NOT i.indisready)
 ) AS catalog_valid,
 bool_and(pg_get_userbyid(t.relowner) = 'paperpilot_test_owner'
      AND t.relkind='r' AND NOT t.relrowsecurity AND NOT t.relforcerowsecurity) AS tables_owned,
 bool_and(has_table_privilege(current_user,t.oid,'SELECT')
      AND has_table_privilege(current_user,t.oid,'INSERT')
      AND has_table_privilege(current_user,t.oid,'UPDATE')
      AND has_table_privilege(current_user,t.oid,'DELETE')
      AND NOT has_table_privilege(current_user,t.oid,'TRUNCATE')) AS dml_only
FROM pg_class t JOIN pg_namespace n ON n.oid=t.relnamespace
WHERE n.nspname='public' AND t.relname IN ('users','workspaces','workspace_members')
"""


def read_snapshot(connection):
    identity = dict(connection.exec_driver_sql(IDENTITY_SQL).mappings().one())
    marker = dict(connection.exec_driver_sql(
        "SELECT environment_id::text AS environment_id, purpose FROM test_control.environment WHERE id=1"
    ).mappings().one())
    revisions = tuple(connection.exec_driver_sql("SELECT version_num FROM public.alembic_version").scalars())
    catalog = dict(connection.exec_driver_sql(CATALOG_SQL).mappings().one())
    return identity, marker, revisions, catalog, read_schema(connection)


def validate_snapshot(snapshot, settings):
    identity, marker, revisions, catalog, schema = snapshot
    exact = {
        "database_name": settings.db_name, "session_user_name": settings.user,
        "current_user_name": settings.user, "address": settings.host, "port": settings.port,
        "search_path": "public", "database_owner": OWNER, "memberships": 0,
        "marker_owner": OWNER, "version_owner": OWNER, "effective_schemas": ["pg_catalog", "public"],
    }
    if any(identity.get(k) != value for k, value in exact.items()):
        raise PgSafetyError("PostgreSQL server identity mismatch")
    if type(identity.get("version")) is not int or not 170000 <= identity["version"] < 180000:
        raise PgSafetyError("Unapproved PostgreSQL server version")
    flags = ("rolsuper", "rolcreatedb", "rolcreaterole", "rolreplication",
             "rolbypassrls", "schema_create", "database_create", "marker_write",
             "database_temp", "marker_schema_create", "version_write")
    if any(identity.get(k) is not False for k in flags) or identity.get("rolcanlogin") is not True:
        raise PgSafetyError("Unexpected PostgreSQL test role privileges")
    if marker != {"environment_id": settings.environment_id, "purpose": PURPOSE}:
        raise PgSafetyError("Missing or mismatched server environment marker")
    if revisions != (REVISION,):
        raise PgSafetyError("Unexpected PostgreSQL test revision")
    if any(catalog.get(k) is not True for k in ("catalog_valid", "tables_owned", "dml_only")):
        raise PgSafetyError("Unsafe PostgreSQL schema or table privileges")
    if schema != expected_schema():
        raise PgSafetyError("PostgreSQL schema does not match the reviewed models")


@dataclass(frozen=True)
class _Lease:
    connection_id: int
    driver_id: int
    engine_ref: object = None

_leases=WeakKeyDictionary()

def revoke(connection):
    # No .info/.connection access: revocation cannot reconnect or depend on cleanup.
    try:_leases.pop(connection,None)
    except TypeError:pass

def revoke_engine(engine):
    for connection,lease in list(_leases.items()):
        if lease.engine_ref is not None and lease.engine_ref() is engine:
            revoke(connection)

def _verify_fresh_connection(connection,settings,opt_in):
    from sqlalchemy.engine import Connection
    from tests.postgres import policy
    revoke(connection)
    failed=False
    try:
        if connection is None or connection.closed or connection.invalidated:
            raise PgSafetyError("A fresh idle connection is required")
        if connection.in_transaction():
            raise PgSafetyError("Verification cannot take over an active transaction")
        # State checks precede .info, and logical revocation precedes preflight.
        connection.info.pop(_TICKET,None)
        preflight(settings,opt_in)
        if connection.dialect.name!="postgresql":
            raise PgSafetyError("PostgreSQL dialect required")
        if isinstance(connection,Connection):
            policy.require_engine(connection.engine)
        connection.exec_driver_sql("SET TRANSACTION READ ONLY")
        connection.exec_driver_sql("SET LOCAL lock_timeout = '5s'")
        connection.exec_driver_sql("SET LOCAL statement_timeout = '15s'")
        validate_snapshot(read_snapshot(connection),settings)
        connection.rollback()
        if connection.in_transaction():
            raise PgSafetyError("Verification transaction did not end")
        owner=ref(connection.engine) if isinstance(connection,Connection) else None
        lease=_Lease(id(connection),id(connection.connection.driver_connection),owner)
        connection.info[_TICKET]=lease
        _leases[connection]=lease
        return
    except Exception:
        failed=True
    if failed:
        revoke(connection)
        if isinstance(connection,Connection):
            policy.trip(connection.engine)
        for name in ("rollback","invalidate","close"):
            try:getattr(connection,name)()
            except Exception:pass
        raise PgSafetyError("PostgreSQL identity verification failed; lease revoked")

def verify_connection(connection,settings,opt_in):
    token=_verifying.set(id(connection))
    try:_verify_fresh_connection(connection,settings,opt_in)
    finally:_verifying.reset(token)

def require_verified(connection):
    if connection is None or connection.closed or connection.invalidated:
        raise PgSafetyError("A live verified test connection is required")
    lease=_leases.get(connection)
    if lease is None:
        raise PgSafetyError("The connection lease is revoked")
    if lease.engine_ref is not None:
        from tests.postgres import policy
        policy.require_engine(lease.engine_ref())
    if (lease.connection_id!=id(connection) or
        lease.driver_id!=id(connection.connection.driver_connection) or
        connection.info.get(_TICKET) is not lease):
        revoke(connection)
        raise PgSafetyError("The connection lease has expired")
