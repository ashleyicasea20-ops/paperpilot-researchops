"""Per-checkout DBAPI handles: native operations share the SQLAlchemy lease gate."""
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import NamedTuple
from weakref import ref, WeakSet

from tests.postgres.errors import PgSafetyError

_acquiring = ContextVar("paperpilot_pg_acquisition", default=None)
_acquisitions = WeakSet()
_executing = ContextVar("paperpilot_pg_dbapi_statement", default=None)
_statements = WeakSet()


@dataclass(eq=False)
class _Statement:
    connection: object
    driver: object
    active: bool = True
    wire: str | None = None
    used: bool = False


@contextmanager
def execution(connection):
    scope = _Statement(connection, connection.connection.driver_connection)
    _statements.add(scope)
    token = _executing.set(scope)
    try:
        yield
    finally:
        scope.active = False
        _statements.discard(scope)
        _executing.reset(token)


def approve_verification_statement(connection, statement, context):
    from tests.postgres import safety
    from sqlalchemy.engine.interfaces import ExecutionContext
    if safety._verifying.get() != id(connection):
        return
    scope = _executing.get()
    fixed = {"SET TRANSACTION READ ONLY", "SET LOCAL lock_timeout = '5s'",
             "SET LOCAL statement_timeout = '15s'", safety.IDENTITY_SQL, safety.CATALOG_SQL,
             "SELECT environment_id::text AS environment_id, purpose FROM test_control.environment WHERE id=1",
             "SELECT version_num FROM public.alembic_version"}
    compiled = getattr(context, "compiled", None)
    readonly = compiled is not None and getattr(compiled.statement, "is_select", False)
    if (type(scope) is not _Statement or scope not in _statements or not scope.active or
            not isinstance(context, ExecutionContext) or context.root_connection is not connection or
            context.unicode_statement != statement or scope.connection is not connection or
            not isinstance(statement, str) or not (statement in fixed or readonly)):
        raise PgSafetyError("Unapproved PostgreSQL attestation query")
    scope.wire = statement


@dataclass(eq=False)
class _Acquisition:
    engine: object
    active: bool = True


@contextmanager
def acquisition(engine):
    scope = _Acquisition(engine)
    _acquisitions.add(scope)
    token = _acquiring.set(scope)
    try:
        yield
    finally:
        scope.active = False
        _acquisitions.discard(scope)
        _acquiring.reset(token)


def require_acquisition(engine):
    from tests.postgres import policy
    policy.require_engine(engine)
    scope = _acquiring.get()
    if (type(scope) is not _Acquisition or scope not in _acquisitions or
            not scope.active or scope.engine is not engine):
        raise PgSafetyError("Raw PostgreSQL pool borrowing is not supported; use Engine.connect")


class _ConnectionInfo(NamedTuple):
    """Snapshot of the only ConnectionInfo field used by this dialect."""

    transaction_status: int


class GuardedDBAPI:
    """No native connection or cursor is returned through supported public APIs."""
    def __init__(self, native, engine, record):
        self.__dict__.update(_native=native, _engine=ref(engine), _record=ref(record),
                             _active=True, _owner=None, _verified=False)

    def __repr__(self):
        return "<guarded PostgreSQL DBAPI handle>"

    @property
    def info(self):
        from psycopg.pq import TransactionStatus

        self._configuration()
        status = self._native.info.transaction_status
        if type(status) not in (int, TransactionStatus):
            raise PgSafetyError("Unsupported PostgreSQL transaction status")
        return _ConnectionInfo(int(status))

    def __getattr__(self, name):
        if name in ("connection", "driver_connection", "dbapi_connection"):
            return self
        if name == "pgconn":
            raise PgSafetyError("Direct native PostgreSQL access is unsupported")
        if name in ("closed", "broken"):
            # The dialect needs these flags to classify a disconnect.
            self._current()
            value = getattr(self._native, name)
            if type(value) is bool:
                return value
        elif name in ("autocommit", "isolation_level", "read_only", "deferrable"):
            from psycopg import IsolationLevel

            self._configuration()
            value = getattr(self._native, name)
            if name == "isolation_level":
                if value is None or type(value) is IsolationLevel:
                    return value
            elif value is None or type(value) is bool:
                return value
        raise AttributeError("Unsupported PostgreSQL DBAPI attribute")

    def __setattr__(self, name, value):
        if name.startswith("_"):
            raise AttributeError("Guard state cannot be assigned through the DBAPI interface")
        if name not in {"autocommit", "isolation_level", "read_only", "deferrable"}:
            raise PgSafetyError("Unsupported PostgreSQL connection configuration")
        self._configuration()
        setattr(self._native, name, value)

    def _current(self):
        record = self._record()
        if record is None or record.dbapi_connection is not self or not self._active:
            raise PgSafetyError("The PostgreSQL DBAPI checkout has expired")

    def _business(self):
        from tests.postgres import policy
        self._current()
        connection = self._owner() if self._owner is not None else None
        if connection is None:
            raise PgSafetyError("An attested SQLAlchemy connection is required")
        policy.require_connection(connection, self._engine())
        from tests.postgres.safety import require_verified
        require_verified(connection)
        if connection.connection.driver_connection is not self:
            raise PgSafetyError("The PostgreSQL DBAPI lease has expired")
        return connection

    def _configuration(self):
        self._current()
        from tests.postgres import policy
        policy.require_engine(self._engine())
        if self._owner is None:
            require_acquisition(self._engine())
        else:
            self._business()

    def _sql(self, statement, *, bound=False, many=False):
        self._current()
        from tests.postgres import policy, cleanup
        if type(statement) is not str:
            raise PgSafetyError("PostgreSQL test execution requires plain SQL text")
        if cleanup.allow_dbapi(self, statement, many=many):
            return
        initializing = policy._initializing.get()
        if initializing is not None and initializing[0] is self._engine() and initializing[2] == id(self):
            require_acquisition(self._engine())
            if (many or statement.strip().lower() not in
                    policy._INITIALIZATION_SQL | {"show transaction isolation level"}):
                raise PgSafetyError("Unapproved raw PostgreSQL initialization query")
            return
        from tests.postgres import safety
        scope = _executing.get()
        verifying = (
            not many and type(scope) is _Statement and scope in _statements
            and scope.active and not scope.used and scope.driver is self
            and scope.wire == statement and safety._verifying.get() == id(scope.connection)
        )
        if verifying:
            policy.require_connection(scope.connection, self._engine())
        else:
            connection = self._business()
            policy.require_statement(connection, statement)
        policy.require_sql_text(statement, bound=bound or many)
        if verifying:
            scope.used = True
            return True

    def cursor(self, *args, **kwargs):
        self._current()
        if kwargs.get("row_factory") is not None:
            raise PgSafetyError("Custom native cursor factories are unsupported")
        return GuardedCursor(self, self._native.cursor(*args, **kwargs))

    def execute(self, statement, parameters=None, **kwargs):
        cursor = self.cursor()
        cursor.execute(statement, parameters, **kwargs)
        return cursor

    def commit(self):
        self._business()
        return self._native.commit()

    def rollback(self):
        # Rollback is cleanup, even after lease revocation or an Engine fuse.
        # A returned/older checkout cannot roll back the next borrower's work.
        self._current()
        return self._native.rollback()

    def close(self):
        record = self._record()
        if record is not None and record.dbapi_connection is not self:
            raise PgSafetyError("An obsolete DBAPI handle cannot close a newer checkout")
        self.__dict__["_active"] = False
        return self._native.close()

    def add_notice_handler(self, handler):
        self._configuration()
        return self._native.add_notice_handler(handler)

    def tpc_begin(self, *args, **kwargs):
        from tests.postgres.cleanup import reject_twophase
        owner = self._owner() if self._owner is not None else None
        reject_twophase(owner)

    tpc_prepare = tpc_commit = tpc_begin

    def tpc_rollback(self, *args, **kwargs):
        from tests.postgres import cleanup
        self._current()
        cleanup.require_twophase_rollback(self)
        return self._native.tpc_rollback(*args, **kwargs)

    def __enter__(self):
        self._business()
        return self

    def __exit__(self, kind, value, traceback):
        if kind is None:
            self.commit()
        else:
            self.rollback()


class GuardedCursor:
    def __init__(self, connection, native):
        self._connection = connection
        self._native = native
        self._statement = None
        self._bound = False
        self._verification_read = False

    @property
    def connection(self):
        return self._connection

    def __getattr__(self, name):
        if name in ("description", "rowcount", "arraysize", "closed"):
            self.connection._current()
            value = getattr(self._native, name)
            if name == "description" and value is not None:
                columns = tuple(tuple(column) for column in value)
                if all(type(field) in (str, int, bool, type(None)) for column in columns for field in column):
                    return columns
            elif type(value) in (int, bool, type(None)):
                return value
        raise AttributeError("Unsupported PostgreSQL cursor attribute")

    def execute(self, statement, parameters=None, **kwargs):
        bound = parameters is not None
        self._verification_read = self.connection._sql(statement, bound=bound) is True
        self._statement = statement
        self._bound = bound
        self._native.execute(statement, parameters, **kwargs)
        return self

    def executemany(self, statement, parameters, **kwargs):
        self.connection._sql(statement, many=True)
        self._statement = statement
        self._bound = True
        self._verification_read = False
        self._native.executemany(statement, parameters, **kwargs)
        return self

    def fetchone(self):
        self._read()
        return self._native.fetchone()

    def fetchmany(self, *args):
        self._read()
        return self._native.fetchmany(*args)

    def fetchall(self):
        self._read()
        return self._native.fetchall()

    def _read(self):
        from tests.postgres import safety, policy
        owner = self.connection._owner() if self.connection._owner is not None else None
        if self._verification_read and owner is not None and safety._verifying.get() == id(owner):
            self.connection._current()
            policy.require_connection(owner)
        else:
            self.connection._sql(self._statement, bound=self._bound)

    def close(self):
        return self._native.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


def bind(connection):
    proxy = connection.connection.driver_connection
    if type(proxy) is not GuardedDBAPI or proxy._engine() is not connection.engine:
        raise PgSafetyError("An instrumented PostgreSQL DBAPI is required")
    proxy._current()
    proxy.__dict__["_owner"] = ref(connection)


def confirmed(connection):
    connection.connection.driver_connection.__dict__["_verified"] = True


def synthetic_driver(proxy, engine):
    from tests.postgres._synthetic import DBAPI
    return (type(proxy) is GuardedDBAPI and proxy._engine() is engine and
            type(proxy._native) is DBAPI)


def attach_pool(engine):
    from sqlalchemy import event
    invoke = engine.pool._invoke_creator
    def creator(record):
        require_acquisition(engine)
        return GuardedDBAPI(invoke(record), engine, record)
    engine.pool._invoke_creator = creator

    @event.listens_for(engine.pool, "checkout")
    def checkout(proxy, record, fairy):
        require_acquisition(engine)
        if type(proxy) is not GuardedDBAPI:
            raise PgSafetyError("Uninstrumented PostgreSQL pool entry")
        # Keep the physical connection, but never revive an old public handle.
        proxy.__dict__["_active"] = False
        current = GuardedDBAPI(proxy._native, engine, record)
        record.dbapi_connection = fairy.dbapi_connection = current

    @event.listens_for(engine.pool, "checkin")
    def checkin(proxy, record):
        if type(proxy) is GuardedDBAPI:
            proxy.__dict__["_active"] = False
