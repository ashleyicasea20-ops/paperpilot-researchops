"""Exercise the real Alembic environment with mocked context, never run SQL."""

from contextlib import nullcontext
from pathlib import Path
import runpy
from unittest.mock import Mock

from alembic import context
from alembic.config import Config
from alembic.util import CommandError
import pytest

from tests.postgres import migrations, safety
from tests.postgres.settings import PgSafetyError

BACKEND = Path(__file__).resolve().parents[2]


def test_missing_injected_connection_is_rejected(settings, opt_in):
    with pytest.raises(PgSafetyError):
        migrations.prepare_test_migration(None, settings, opt_in)


@pytest.mark.parametrize("kind", ["missing", "unverified", "replaced", "stale", "wrong-revision"])
def test_test_migration_can_never_fall_back(
    settings, opt_in, snapshot, mock_connection, monkeypatch, kind,
):
    monkeypatch.setattr(safety, "read_snapshot", Mock(return_value=snapshot))
    if kind in ("missing", "unverified"):
        config = Config(str(BACKEND / "alembic.ini"))
        config.attributes[migrations.TEST_MODE] = True
        if kind == "unverified":
            config.attributes["connection"] = mock_connection
    else:
        config = migrations.prepare_test_migration(mock_connection, settings, opt_in)
        if kind == "replaced":
            config.attributes["connection"] = Mock()
        elif kind == "stale":
            mock_connection.info.clear()
    fallback = Mock(side_effect=AssertionError("Default Engine fallback"))
    monkeypatch.setattr("app.db.session.get_engine", fallback)
    monkeypatch.setattr(context, "config", config, raising=False)
    monkeypatch.setattr(context, "is_offline_mode", lambda: False)
    monkeypatch.setattr(context, "get_revision_argument", lambda: "head" if kind == "wrong-revision" else safety.REVISION)
    run = Mock()
    monkeypatch.setattr(context, "run_migrations", run)
    with pytest.raises(CommandError) as caught:
        runpy.run_path(str(BACKEND / "alembic/env.py"))
    fallback.assert_not_called()
    run.assert_not_called()
    assert caught.value.__context__ is None


def test_verified_lease_reaches_only_injected_connection(
    settings, opt_in, snapshot, mock_connection, monkeypatch,
):
    monkeypatch.setattr(safety, "read_snapshot", Mock(return_value=snapshot))
    config = migrations.prepare_test_migration(mock_connection, settings, opt_in)
    fallback = Mock(side_effect=AssertionError("Default Engine fallback"))
    monkeypatch.setattr("app.db.session.get_engine", fallback)
    monkeypatch.setattr(context, "config", config, raising=False)
    monkeypatch.setattr(context, "is_offline_mode", lambda: False)
    monkeypatch.setattr(context, "get_revision_argument", lambda: safety.REVISION)
    configure, run = Mock(), Mock()
    monkeypatch.setattr(context, "configure", configure)
    monkeypatch.setattr(context, "begin_transaction", nullcontext)
    monkeypatch.setattr(context, "run_migrations", run)
    runpy.run_path(str(BACKEND / "alembic/env.py"))
    assert configure.call_args.kwargs["connection"] is mock_connection
    run.assert_called_once_with()
    fallback.assert_not_called()
