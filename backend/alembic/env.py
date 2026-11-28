"""Alembic metadata and connection setup. Invoked only by Alembic commands."""

from alembic import context
from alembic.util import CommandError

from app.db.base import Base
import app.models  # noqa: F401 -- register all tables before configuring Alembic

config = context.config
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Compile PostgreSQL SQL without settings, credentials, or a connection."""
    context.configure(
        dialect_name="postgresql",
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def configure_connection(connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Reuse an explicitly supplied connection or the application's lazy engine.

    Online schema changes require separate human approval. Merely importing the
    application or models never loads this file or runs a migration.
    """
    error_type = None
    try:
        connection = config.attributes.get("connection")
        if config.attributes.get("paperpilot_pg_test"):
            from tests.postgres.migrations import require_test_migration_connection
            require_test_migration_connection(config, connection, context.get_revision_argument())
        if connection is not None:
            configure_connection(connection)
        else:
            from app.db.session import get_engine

            with get_engine().connect() as connection:
                configure_connection(connection)
    except Exception as exc:
        # Driver/settings exceptions may embed credentials. Do not pass their
        # message, traceback, or exception context into Alembic's CLI reporter.
        error_type = type(exc).__name__
    if error_type is not None:
        raise CommandError(
            f"Online migration failed ({error_type}); details suppressed. "
            "Check local configuration and database state before retrying."
        ) from None


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
