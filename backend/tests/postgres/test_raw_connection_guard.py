"""G1: public raw, PoolProxy and DBAPI operations must not escape leases."""
import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session
from tests.postgres import policy, safety
from tests.postgres.errors import PgSafetyError


@pytest.mark.parametrize("entry", ["raw_connection", "pool_connect"])
def test_unverified_raw_checkout_is_refused_before_sql(guard_lab, entry):
    with guard_lab() as case:
        get = case.engine.raw_connection if entry == "raw_connection" else case.engine.pool.connect
        borrowed = None
        try:
            with pytest.raises(PgSafetyError):
                borrowed = get()
        finally:
            if borrowed is not None:
                borrowed.close()
        assert case.lab.sql == []


def test_normal_connection_and_single_phase_commit_still_work(guard_lab):
    with guard_lab() as case:
        with case.engine.connect() as connection:
            assert connection.exec_driver_sql("SELECT 1").scalar() == 1
            connection.commit()
        assert case.lab.commits == 1


@pytest.mark.parametrize("entry", ["driver", "pool_proxy", "cursor", "executemany"])
def test_unverified_dbapi_cannot_execute(guard_lab, entry):
    with guard_lab() as case:
        with case.engine.connect() as connection:
            proxy = connection.connection
            driver = proxy.driver_connection
            cursor = driver.cursor()
            safety.revoke(connection)
            before = len(case.lab.sql)
            with pytest.raises(PgSafetyError):
                if entry == "driver": driver.cursor().execute("UPDATE demo SET value=1")
                elif entry == "pool_proxy": proxy.cursor().execute("UPDATE demo SET value=1")
                elif entry == "cursor": cursor.execute("UPDATE demo SET value=1")
                else: cursor.executemany("UPDATE demo SET value=1", [()])
            assert len(case.lab.sql) == before


@pytest.mark.parametrize("entry", ["cursor", "commit", "session", "driver_sql"])
def test_fuse_stops_already_borrowed_interfaces(guard_lab, entry):
    with guard_lab() as case:
        with Session(case.engine) as session:
            connection = session.connection()
            driver = connection.connection.driver_connection
            cursor = driver.cursor()
            policy.trip(case.engine)
            before = len(case.lab.sql), case.lab.commits
            with pytest.raises(PgSafetyError):
                if entry == "cursor": cursor.execute("UPDATE demo SET value=1")
                elif entry == "commit": driver.commit()
                elif entry == "session": session.execute(text("UPDATE demo SET value=1"))
                else: connection.exec_driver_sql("UPDATE demo SET value=1")
            assert (len(case.lab.sql), case.lab.commits) == before
            driver.rollback()


@pytest.mark.parametrize("operation", ["execute", "commit", "rollback"])
def test_returned_dbapi_handle_does_not_revive_on_recheckout(guard_lab, operation):
    with guard_lab() as case:
        with case.engine.connect() as first:
            stale = first.connection.driver_connection
            cursor = stale.cursor()
        with case.engine.connect() as second:
            safety.require_verified(second)
            before = len(case.lab.sql), case.lab.commits, case.lab.rollbacks
            with pytest.raises(PgSafetyError):
                if operation == "execute": cursor.execute("UPDATE demo SET value=1")
                else: getattr(stale, operation)()
            assert (len(case.lab.sql), case.lab.commits, case.lab.rollbacks) == before
            assert second.exec_driver_sql("SELECT 1").scalar() == 1
        assert case.lab.created == 1


def test_forged_acquisition_context_does_not_authorize_raw_checkout(guard_lab):
    from tests.postgres import dbapi
    with guard_lab() as case:
        fake = dbapi._Acquisition(case.engine)
        token = dbapi._acquiring.set(fake)
        try:
            with pytest.raises(PgSafetyError): case.engine.raw_connection()
            with pytest.raises(PgSafetyError): case.engine.pool.connect()
        finally: dbapi._acquiring.reset(token)
        assert case.lab.created == 0


def test_attestation_does_not_grant_raw_dbapi_sql_or_commit(guard_lab, monkeypatch, snapshot):
    with guard_lab() as case:
        def inspect(connection):
            driver = connection.connection.driver_connection
            before = len(case.lab.sql), case.lab.commits
            with pytest.raises(PgSafetyError): driver.cursor().execute("UPDATE demo SET value=1")
            with pytest.raises(PgSafetyError): driver.commit()
            with pytest.raises(PgSafetyError): driver.autocommit = True
            assert (len(case.lab.sql), case.lab.commits) == before
            # Real SQLAlchemy verification controls still pass through the gate.
            assert connection.exec_driver_sql(safety.IDENTITY_SQL).scalar() == 1
            return snapshot
        monkeypatch.setattr(safety, "read_snapshot", inspect)
        with case.engine.connect() as connection:
            assert connection.exec_driver_sql("SELECT 1").scalar() == 1


def test_cursor_connection_cannot_unwrap_the_guard(guard_lab):
    with guard_lab() as case:
        with case.engine.connect() as connection:
            driver = connection.connection.driver_connection
            cursor = driver.cursor()
            assert cursor.connection is driver
            with pytest.raises(AttributeError): cursor.connection = object()
            with pytest.raises(PgSafetyError): driver.cursor_factory = object()
            with pytest.raises(PgSafetyError): driver.cursor(row_factory=lambda _: None)
            policy.trip(case.engine)
            with pytest.raises(PgSafetyError): cursor.connection.commit()
            assert case.lab.commits == 0


@pytest.fixture
def psycopg_metadata(monkeypatch):
    from psycopg import ConnectionInfo
    from psycopg.pq import TransactionStatus
    from tests.postgres._synthetic import DBAPI

    class SyntheticPQ:
        transaction_status = TransactionStatus.IDLE

        @property
        def password(self):
            pytest.fail("The metadata guard must not read the password")

    transport = SyntheticPQ()
    monkeypatch.setattr(DBAPI, "info", property(lambda _: ConnectionInfo(transport)), raising=False)
    monkeypatch.setattr(DBAPI, "adapters", transport, raising=False)
    return transport


def test_verified_metadata_is_read_only_and_contains_only_safe_fields(guard_lab, psycopg_metadata):
    from psycopg.pq import TransactionStatus

    with guard_lab() as case, case.engine.connect() as connection:
        driver = connection.connection.driver_connection
        info = driver.info
        assert info.transaction_status == TransactionStatus.IDLE
        psycopg_metadata.transaction_status = TransactionStatus.INTRANS
        assert driver.info.transaction_status == TransactionStatus.INTRANS
        with pytest.raises(AttributeError):
            info.transaction_status = TransactionStatus.ACTIVE
        for name in ("pgconn", "password", "dsn", "get_parameters"):
            with pytest.raises(AttributeError):
                getattr(info, name)
        for name in ("pgconn", "adapters", "cursor_factory", "unreviewed"):
            with pytest.raises((AttributeError, PgSafetyError)):
                getattr(driver, name)


@pytest.mark.parametrize("phase", ["verification", "revoked", "fused", "returned", "invalidated"])
def test_metadata_reads_require_a_current_verified_checkout(
    guard_lab, psycopg_metadata, phase, monkeypatch, snapshot,
):
    with guard_lab() as case:
        if phase == "verification":
            def inspect(connection):
                with pytest.raises(PgSafetyError):
                    _ = connection.connection.driver_connection.info
                return snapshot

            monkeypatch.setattr(safety, "read_snapshot", inspect)
            with case.engine.connect() as connection:
                assert connection.connection.driver_connection.info.transaction_status == 0
            return

        with case.engine.connect() as connection:
            driver = connection.connection.driver_connection
            cached_info = driver.info
            if phase == "revoked":
                safety.revoke(connection)
            elif phase == "fused":
                connection.exec_driver_sql("UPDATE demo SET value=1")
                assert connection.in_transaction()
                # Fail a second verification while this checkout is still busy.
                identity, *rest = snapshot
                monkeypatch.setattr(
                    safety, "read_snapshot",
                    lambda _: (dict(identity, database_name="synthetic-other-db"), *rest),
                )
                with pytest.raises(PgSafetyError):
                    case.engine.connect()
            elif phase == "returned":
                connection.close()
            else:
                connection.invalidate()

            before = case.lab.created, len(case.lab.sql), case.lab.commits
            with pytest.raises(PgSafetyError):
                _ = driver.info
            with pytest.raises(AttributeError):
                _ = cached_info.pgconn
            assert (case.lab.created, len(case.lab.sql), case.lab.commits) == before

        if phase == "returned":
            with case.engine.connect() as reused:
                assert case.lab.created == 1
                assert reused.connection.driver_connection.info.transaction_status == 0
                with pytest.raises(PgSafetyError):
                    _ = driver.info


def test_actual_psycopg_dialect_first_connect_with_guarded_dbapi(
    guard_lab, settings, opt_in, monkeypatch, psycopg_metadata,
):
    from sqlalchemy import create_engine
    from tests.postgres import engine as engines
    from tests.postgres._synthetic import DBAPI

    monkeypatch.setattr(DBAPI, "add_notice_handler", lambda *a: None, raising=False)
    monkeypatch.setattr(DBAPI, "closed", False, raising=False)
    monkeypatch.setattr(DBAPI, "broken", False, raising=False)
    with guard_lab() as case:
        actual = create_engine("postgresql+psycopg://", creator=case.lab.connect,
                               use_native_hstore=False, isolation_level="READ COMMITTED")
        engines._attach(actual, settings, opt_in, mode="synthetic")
        try:
            with actual.connect() as connection:
                safety.require_verified(connection)
                assert connection.get_isolation_level() == "READ COMMITTED"
                assert connection.exec_driver_sql("SELECT 1").scalar() == 1
                connection.commit()
                assert actual.dialect.server_version_info == (17, 12)
                assert actual.dialect.default_isolation_level == "READ COMMITTED"
            assert actual.pool.checkedout() == 0 and case.lab.commits == 1
        finally: actual.dispose()
