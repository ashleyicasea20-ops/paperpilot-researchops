"""Registered Engine provenance and narrowly bounded SQL execution checks."""
from contextvars import ContextVar
from dataclasses import dataclass
import re
from weakref import WeakKeyDictionary

from tests.postgres import runtime
from tests.postgres.errors import PgSafetyError

@dataclass(eq=False)
class EngineState:
    mode: str
    owner: object = None
    settings: object = None
    fault: bool = False
    failed: bool = False
    closed: bool = False
    attested: bool = False

_engines = WeakKeyDictionary()
_initializing = ContextVar("paperpilot_pg_initialization", default=None)
_INITIALIZATION_SQL = frozenset((
    "select pg_catalog.version()",
    "select current_schema()",
    "show standard_conforming_strings",
))
_TWO_PHASE = {("PREPARE", "TRANSACTION"), ("COMMIT", "PREPARED"), ("ROLLBACK", "PREPARED")}
# PostgreSQL identifiers can contain non-ASCII characters and interior dollars.
_WORD = re.compile(r"[A-Za-z_\x80-\U0010ffff][A-Za-z_0-9$\x80-\U0010ffff]*")
_DOLLAR_QUOTE = re.compile(r"\$(?:[A-Za-z_\x80-\U0010ffff][A-Za-z_0-9\x80-\U0010ffff]*)?\$")
_PARAMETER = re.compile(r"\$[0-9]+")
_STRING = re.compile(r"'(?:[^'\\]|'')*+'")
_ESCAPE_STRING = re.compile(r"'(?:[^'\\]|''|\\[\s\S])*+'")
_QUOTED_IDENTIFIER = re.compile(r'"(?:[^"]|"")*+"')
_COMMENT_BOUNDARY = re.compile(r"/\*|\*/")
_LINE_END = re.compile(r"[\r\n]")


def _unclassified():
    return PgSafetyError("Cannot classify PostgreSQL test SQL text")


def _comment_end(statement, start):
    depth = 1
    for boundary in _COMMENT_BOUNDARY.finditer(statement, start + 2):
        depth += 1 if boundary[0] == "/*" else -1
        if depth == 0:
            return boundary.end()
    raise _unclassified()


def _command(statement):
    """Identify the prefix of a single command without parsing its SQL grammar."""
    prefix = []
    terminated = False
    index = 0
    while index < len(statement):
        char = statement[index]
        if char in " \t\r\n\f\v":
            index += 1
            continue
        if statement.startswith("--", index):
            end = _LINE_END.search(statement, index + 2)
            index = end.start() if end else len(statement)
            continue
        if statement.startswith("/*", index):
            index = _comment_end(statement, index)
            continue
        if terminated:
            raise PgSafetyError("Multiple SQL statements are unsupported by PostgreSQL test Engines")
        if char == ";" and prefix:
            terminated = True
            index += 1
            continue

        word = _WORD.match(statement, index)
        token = word[0].upper() if word and word[0].isascii() else None
        if word:
            index = word.end()
            if token == "E" and statement.startswith("'", index):
                literal = _ESCAPE_STRING.match(statement, index)
                if literal is None:
                    raise _unclassified()
                index, token = literal.end(), None
        elif char in "'\"":
            # A backslash in an ordinary string depends on the session setting.
            pattern = _STRING if char == "'" else _QUOTED_IDENTIFIER
            literal = pattern.match(statement, index)
            if literal is None:
                raise _unclassified()
            index = literal.end()
        elif char == "$":
            delimiter = _DOLLAR_QUOTE.match(statement, index)
            if delimiter:
                end = statement.find(delimiter[0], delimiter.end())
                if end == -1:
                    raise _unclassified()
                index = end + len(delimiter[0])
            else:
                parameter = _PARAMETER.match(statement, index)
                if parameter is None:
                    raise _unclassified()
                index = parameter.end()
        elif char in "0123456789(),[].:+-*/%^<>=~!@#&|`?":
            if char == "(" and not prefix:
                token = char  # PostgreSQL permits a parenthesized SELECT.
            index += 1
        else:
            raise _unclassified()

        if not prefix and token is None:
            raise _unclassified()
        if len(prefix) < 2:
            prefix.append(token)
    if not prefix:
        raise _unclassified()
    return tuple(prefix)


def require_sql_text(statement, *, bound):
    """Apply the text policy to the command Psycopg's server-binding cursor sends."""
    if type(statement) is not str or "\x00" in statement:
        raise _unclassified()
    if bound and "%" in statement:
        from psycopg import ProgrammingError
        from psycopg._queries import _query2pg_nocache

        # Use the pinned driver's placeholder conversion so quoted/commented
        # text is classified after the same percent rewriting as the wire SQL.
        try:
            wire, *_ = _query2pg_nocache(statement.encode("utf-8"), "utf-8")
            statement = wire.decode("utf-8")
        except (ProgrammingError, UnicodeError):
            statement = None
        if statement is None:
            raise _unclassified()
    if _command(statement) in _TWO_PHASE:
        raise PgSafetyError("Two-phase transactions are unsupported by PostgreSQL test Engines")

def register(engine, *, mode, settings, owner=None, fault=False):
    if mode not in ("live", "synthetic"):
        raise PgSafetyError("Unknown PostgreSQL Engine mode")
    record = EngineState(mode, owner, settings, fault)
    _engines[engine] = record
    return record

def state(engine):
    try:
        return _engines.get(engine)
    except TypeError:
        return None

def require_engine(engine, *, live=False):
    record = state(engine)
    if record is None or record.closed or record.failed:
        raise PgSafetyError("Unregistered, closed or failed PostgreSQL test Engine")
    if live and record.mode != "live":
        raise PgSafetyError("Offline test doubles cannot authorize physical connections")
    if record.mode == "live":
        runtime.check(record.owner, fault=record.fault)
    return record

def _origin(connection, engine=None):
    from sqlalchemy.engine import Connection
    actual = connection.engine if isinstance(connection, Connection) else engine
    record = require_engine(actual)
    if isinstance(connection, Connection):
        if connection.closed or connection.invalidated:
            raise PgSafetyError("Closed or invalidated connection")
        if record.mode == "synthetic":
            from tests.postgres.dbapi import synthetic_driver
            if not synthetic_driver(connection.connection.driver_connection,actual):
                raise PgSafetyError("Synthetic permission cannot authorize a native DBAPI")
        if engine is not None and actual is not engine:
            raise PgSafetyError("Connection belongs to a different Engine")
    elif record.mode != "synthetic":
        raise PgSafetyError("Live attestation requires an actual SQLAlchemy Connection")
    return actual

def require_connection(connection, engine=None):
    _origin(connection, engine)
    from tests.postgres.safety import _verifying, require_verified
    if _verifying.get() != id(connection):
        require_verified(connection)

def require_statement(connection, statement, engine=None, *, context=None, parameters=None):
    """Initialization allows only the pinned dialect's three fixed read queries."""
    from sqlalchemy.engine import Connection
    from tests.postgres import cleanup
    if isinstance(connection,Connection) and cleanup.allow_statement(connection,statement,context,parameters):
        if engine is not None and connection.engine is not engine:
            raise PgSafetyError("Cleanup cannot cross PostgreSQL Engines")
        return
    initializing = _initializing.get()
    if initializing is not None and initializing[1] == id(connection):
        actual = _origin(connection, engine)
        if (initializing[0] is not actual or
                initializing[2] != id(connection.connection.driver_connection) or
                not isinstance(statement, str) or
                statement.strip().lower() not in _INITIALIZATION_SQL):
            raise PgSafetyError("Unapproved PostgreSQL initialization statement")
        return
    require_connection(connection, engine)

def trip(engine):
    record = state(engine)
    if record is not None:
        record.failed = True
        record.attested = False
    from tests.postgres.safety import revoke_engine
    revoke_engine(engine)

def finish(engine):
    record = state(engine)
    if record is not None:
        record.closed = True
        record.attested = False
    from tests.postgres.safety import revoke_engine
    revoke_engine(engine)
