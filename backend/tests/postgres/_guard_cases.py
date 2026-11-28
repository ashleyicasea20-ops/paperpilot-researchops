"""Independent SQL/counter simulator used by the G1-G3 regressions."""

class Cursor:
    rowcount = 1
    description = None

    def __init__(self, lab):
        self.lab = lab

    def execute(self, sql, parameters=None):
        self.lab.sql.append(sql)
        self.description = [("value", None, None, None, None, None, None)]
        self.value = {
            "select pg_catalog.version()": "PostgreSQL 17.12 on synthetic guard driver",
            "select current_schema()": "public",
            "show transaction isolation level": "read committed",
            "show standard_conforming_strings": "on",
        }.get(sql.strip().lower(), 1)

    def executemany(self, sql, parameters):
        self.execute(sql, parameters)

    def fetchone(self):
        return (self.value,)

    def close(self):
        pass


class Lab:
    def __init__(self):
        self.sql = self.statements = []
        self.created = self.commits = self.rollbacks = self.closes = 0
        self.tpc_begins = self.tpc_prepares = self.tpc_commits = self.tpc_rollbacks = 0

    def connect(self):
        from tests.postgres._synthetic import DBAPI
        self.created += 1
        return DBAPI(self)
