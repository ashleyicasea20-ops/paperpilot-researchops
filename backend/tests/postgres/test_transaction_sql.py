"""Single-statement policy at the SQLAlchemy and guarded DBAPI boundaries."""
import pytest
from psycopg import sql
from sqlalchemy import text

from tests.postgres import policy
from tests.postgres.errors import PgSafetyError


@pytest.mark.parametrize("entry", ["core", "driver_sql", "dbapi"])
@pytest.mark.parametrize("command", ["PREPARE TRANSACTION", "COMMIT PREPARED", "ROLLBACK PREPARED"])
def test_two_phase_text_is_refused(guard_lab, entry, command):
    statement = f"{command} 'synthetic-xid'"
    with guard_lab(psycopg_dialect=True) as case, case.engine.connect() as connection:
        before = list(case.lab.sql)
        cursor = connection.connection.driver_connection.cursor()
        execute = {
            "core": lambda: connection.execute(text(statement)),
            "driver_sql": lambda: connection.exec_driver_sql(statement),
            "dbapi": lambda: cursor.execute(statement),
        }
        with pytest.raises(PgSafetyError, match="Two-phase") as error:
            execute[entry]()
        assert "synthetic-xid" not in str(error.value)
        assert case.lab.sql == before


@pytest.mark.parametrize("statement", [
    "\tPrePaRe\nTrAnSaCtIoN 'synthetic-xid'",
    "-- leading\n CoMmIt /* outer /* nested */ done */ PrEpArEd 'synthetic-xid'",
    "rollback -- split\r\n prepared 'synthetic-xid'",
    "SELECT ';'; PREPARE TRANSACTION 'synthetic-xid'",
    "SELECT 1; SELECT 2",
    "SELECT 1;;",
])
def test_controls_and_multiple_statements_are_refused(guard_lab, statement):
    with guard_lab() as case, case.engine.connect() as connection:
        before = list(case.lab.sql)
        with pytest.raises(PgSafetyError):
            connection.exec_driver_sql(statement)
        assert case.lab.sql == before


@pytest.mark.parametrize("statement", [
    "SELECT 'PREPARE TRANSACTION; it''s data'",
    r"SELECT E'it\'s data; COMMIT PREPARED'",
    "SELECT $tag$ROLLBACK PREPARED; 'quoted' -- data$tag$",
    'SELECT "COMMIT PREPARED;" FROM demo',
    "/* COMMIT PREPARED; /* nested */ */ SELECT 1; -- ROLLBACK PREPARED",
    "PREPARE synthetic_query AS SELECT 1",
    "COMMIT",
    "ROLLBACK",
    "SELECT 7 %% %s",
    "SELECT %(value)s",
    "(SELECT 1)",
])
def test_non_transaction_sql_is_not_misclassified(guard_lab, statement):
    with guard_lab() as case, case.engine.connect() as connection:
        before = len(case.lab.sql)
        params = {"value": 1} if "%(value)s" in statement else (2,) if "%s" in statement else None
        cursor = connection.connection.driver_connection.cursor()
        cursor.execute(statement, params)
        assert cursor.fetchone() == (1,)
        assert case.lab.sql[before:] == [statement]


@pytest.mark.parametrize("statement", [
    b"SELECT 1",
    sql.SQL("SELECT 1"),
    "SELECT 'unterminated",
    "SELECT /* unterminated",
    "SELECT $tag$unterminated",
    r"SELECT 'ambiguous\backslash'",
    "SELECT 1\x00",
    "SELECT %(malformed)q",
])
def test_unclassifiable_driver_input_is_refused(guard_lab, statement):
    with guard_lab() as case, case.engine.connect() as connection:
        before = list(case.lab.sql)
        cursor = connection.connection.driver_connection.cursor()
        with pytest.raises(PgSafetyError):
            cursor.execute(statement, ())
        assert case.lab.sql == before


def test_normal_core_dml_commit_rollback_and_savepoint(guard_lab):
    with guard_lab(psycopg_dialect=True) as case, case.engine.connect() as connection:
        before = len(case.lab.sql)
        rollbacks = case.lab.rollbacks
        assert connection.execute(text("SELECT 1")).scalar() == 1
        connection.execute(text("INSERT INTO demo (value) VALUES (:value)"), {"value": 1})
        with connection.begin_nested() as nested:
            connection.execute(text("UPDATE demo SET value=:value"), {"value": 2})
            nested.rollback()
        connection.commit()
        connection.execute(text("UPDATE demo SET value=:value"), {"value": 3})
        connection.rollback()
        assert [statement.split()[0] for statement in case.lab.sql[before:]] == [
            "SELECT", "INSERT", "SAVEPOINT", "UPDATE", "ROLLBACK", "UPDATE",
        ]
        assert case.lab.commits == 1 and case.lab.rollbacks == rollbacks + 1


def test_batch_and_fused_execution_remain_bounded(guard_lab):
    with guard_lab() as case, case.engine.connect() as connection:
        connection.exec_driver_sql("SELECT 1")
        assert connection.in_transaction()
        before = list(case.lab.sql)
        cursor = connection.connection.driver_connection.cursor()
        with pytest.raises(PgSafetyError, match="Two-phase"):
            cursor.executemany("COMMIT PREPARED 'synthetic-xid'", [()])
        policy.trip(case.engine)
        with pytest.raises(PgSafetyError):
            cursor.execute("SELECT 1")
        with pytest.raises(PgSafetyError):
            connection.commit()
        assert case.lab.sql == before and case.lab.commits == 0


def test_refused_execution_does_not_change_previous_result_state(guard_lab):
    with guard_lab() as case, case.engine.connect() as connection:
        cursor = connection.connection.driver_connection.cursor()
        cursor.execute("SELECT 7 % 2")
        before = list(case.lab.sql)
        with pytest.raises(PgSafetyError, match="Two-phase"):
            cursor.execute("COMMIT PREPARED 'synthetic-xid'", ())
        assert cursor.fetchone() == (1,)
        assert case.lab.sql == before
