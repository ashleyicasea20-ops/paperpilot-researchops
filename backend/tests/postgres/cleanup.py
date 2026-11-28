"""One-shot cleanup capability derived from actual SQLAlchemy transactions."""
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from weakref import WeakSet

from sqlalchemy.engine import NestedTransaction, TwoPhaseTransaction
from sqlalchemy.sql.elements import RollbackToSavepointClause

from tests.postgres.errors import PgSafetyError


@dataclass(eq=False)
class _Cleanup:
    transaction: object
    connection: object
    driver: object
    name: str
    active: bool = True
    clause: object = None
    wire: str | None = None
    executed: bool = False


_savepoint = ContextVar("paperpilot_pg_savepoint_cleanup", default=None)
_twophase = ContextVar("paperpilot_pg_twophase_cleanup", default=None)
_issued = WeakSet()


def _driver(connection):
    from tests.postgres import policy
    from tests.postgres.dbapi import GuardedDBAPI
    if connection.closed or connection.invalidated or policy.state(connection.engine) is None:
        raise PgSafetyError("A current PostgreSQL checkout is required for cleanup")
    driver = connection.connection.driver_connection
    if type(driver) is not GuardedDBAPI or not driver._verified or driver._owner() is not connection:
        raise PgSafetyError("Cleanup requires a previously attested checkout")
    driver._current()
    return driver


@contextmanager
def savepoint(transaction):
    connection = transaction.connection
    if (not isinstance(transaction, NestedTransaction) or not transaction.is_active or
            connection.get_nested_transaction() is not transaction or
            connection.get_transaction() is None or not connection.get_transaction().is_active):
        raise PgSafetyError("Only the current active SAVEPOINT may be rolled back")
    scope = _Cleanup(transaction, connection, _driver(connection), transaction._savepoint)
    _issued.add(scope)
    token = _savepoint.set(scope)
    try:
        yield
    finally:
        scope.active = False
        _issued.discard(scope)
        _savepoint.reset(token)


def _valid(scope):
    if type(scope) is not _Cleanup or scope not in _issued or not scope.active:
        return False
    connection = scope.connection
    return (not connection.closed and not connection.invalidated and
            connection.get_nested_transaction() is scope.transaction and scope.transaction.is_active and
            connection.get_transaction() is not None and connection.get_transaction().is_active and
            _driver(connection) is scope.driver and scope.transaction._savepoint == scope.name)


def allow_statement(connection, statement, context=None, parameters=None):
    scope = _savepoint.get()
    if not _valid(scope) or scope.connection is not connection:
        return False
    if type(statement) is RollbackToSavepointClause and statement.ident == scope.name:
        if scope.clause is not None:
            return False
        scope.clause = statement
        scope.wire = str(statement.compile(dialect=connection.dialect))
        return True
    return (isinstance(statement, str) and statement == scope.wire and context is not None and
            context.compiled is not None and context.compiled.statement is scope.clause and
            not parameters and not scope.executed)


def allow_dbapi(driver, statement, *, many):
    scope = _savepoint.get()
    if (many or not _valid(scope) or scope.driver is not driver or scope.clause is None or
            statement != scope.wire or scope.executed):
        return False
    scope.executed = True
    return True


@contextmanager
def twophase_rollback(connection, xid, *, recover=False):
    transaction = connection.get_transaction()
    if (recover or not isinstance(transaction, TwoPhaseTransaction) or not transaction.is_active or
            transaction.xid != xid):
        raise PgSafetyError("Only an existing two-phase transaction may be rolled back")
    scope = _Cleanup(transaction, connection, _driver(connection), xid)
    _issued.add(scope)
    token = _twophase.set(scope)
    try:
        yield
    finally:
        scope.active = False
        _issued.discard(scope)
        _twophase.reset(token)


def require_twophase_rollback(driver):
    scope = _twophase.get()
    if (type(scope) is not _Cleanup or scope not in _issued or not scope.active or scope.driver is not driver or
            scope.executed or scope.connection.get_transaction() is not scope.transaction or
            not scope.transaction.is_active or scope.transaction.xid != scope.name or
            _driver(scope.connection) is not driver):
        raise PgSafetyError("Unapproved two-phase transaction cleanup")
    scope.executed = True


def reject_twophase(connection=None, xid=None):
    """Abort only a known local legacy transaction; never reconcile foreign XIDs."""
    from tests.postgres import policy
    transaction = connection.get_transaction() if connection is not None else None
    if (connection is not None and policy.state(connection.engine) is not None and
            isinstance(transaction, TwoPhaseTransaction) and transaction.is_active and
            (xid is None or transaction.xid == xid)):
        try:
            transaction.rollback()
        except Exception:
            policy.trip(connection.engine)
            try:connection.invalidate()
            except Exception:pass
    raise PgSafetyError("Two-phase transactions are unsupported by PostgreSQL test Engines")
