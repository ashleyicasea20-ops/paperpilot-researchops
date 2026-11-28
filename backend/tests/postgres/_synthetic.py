"""Synthetic DBAPI for lifecycle tests; no network code or real credentials."""
from types import SimpleNamespace
from sqlalchemy.engine import Engine, URL
from sqlalchemy.engine.default import DefaultDialect
from sqlalchemy.pool import QueuePool

class Cursor:
    rowcount = 1
    description = None
    def __init__(self, owner):
        self.owner = owner
    def execute(self, statement, parameters=None):
        self.owner.statements.append(statement.split()[0].upper())
        self.description = [("one",None,None,None,None,None,None)] if statement.lstrip().upper().startswith("SELECT") else None
    def fetchone(self):
        return (1,)
    def close(self):
        pass

class DBAPI:
    def __init__(self, lab):
        self.lab = lab
        self.statements = lab.statements
    def cursor(self):
        return Cursor(self)
    def rollback(self):
        pass
    def commit(self):
        self.lab.commits += 1
    def close(self):
        pass

class Lab:
    def __init__(self):
        self.created = 0
        self.commits = 0
        self.statements = []
    def connect(self):
        self.created += 1
        return DBAPI(self)

def bare_engine(lab):
    dialect = DefaultDialect()
    dialect.name = "postgresql"
    dialect.driver = "synthetic"
    return Engine(QueuePool(lab.connect,pool_size=4,max_overflow=0),dialect,
                  URL.create("postgresql+psycopg",database="paperpilot_dev"),hide_parameters=True)

def policy_engine(settings, intent):
    # This compatibility branch captures the original behavior for red tests.
    # The repaired implementation supplies a separate, non-network factory.
    from tests.postgres import engine as module
    if hasattr(module, "create_offline_test_engine"):
        return module.create_offline_test_engine(settings, intent)
    from sqlalchemy import event
    policy = module.create_test_engine(settings, intent)
    lab = Lab()
    physical = bare_engine(lab)
    event.listen(physical,"engine_connect",lambda connection: policy.dispatch.engine_connect(connection))
    event.listen(physical,"before_cursor_execute",
                 lambda *args: policy.dispatch.before_cursor_execute(*args))
    physical._review_lab = lab
    physical._review_policy = policy
    return physical
