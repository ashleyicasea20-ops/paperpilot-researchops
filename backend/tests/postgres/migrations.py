"""Prepare a pinned test migration binding; this module never invokes Alembic commands."""

from dataclasses import dataclass
from pathlib import Path

from alembic.config import Config

from tests.postgres.safety import REVISION, _TICKET, require_verified, verify_connection
from tests.postgres.settings import PgSafetyError

TEST_MODE = "paperpilot_pg_test"
_BINDING = "paperpilot_pg_migration_binding"


@dataclass(frozen=True)
class _Binding:
    connection: object
    lease: object


def prepare_test_migration(connection, settings, opt_in):
    if connection is None:
        raise PgSafetyError("Test migrations require an injected verified connection")
    verify_connection(connection, settings, opt_in)
    config = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
    config.attributes.update({
        TEST_MODE: True, "connection": connection,
        _BINDING: _Binding(connection, connection.info[_TICKET]),
    })
    return config


def require_test_migration_connection(config, connection, revision):
    binding = config.attributes.get(_BINDING)
    if not isinstance(binding, _Binding) or binding.connection is not connection:
        raise PgSafetyError("Missing or replaced test migration connection")
    require_verified(connection)
    if connection.info.get(_TICKET) is not binding.lease or revision != REVISION:
        raise PgSafetyError("Stale migration lease or unapproved target revision")
