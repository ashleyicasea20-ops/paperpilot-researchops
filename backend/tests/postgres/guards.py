"""Layered supported-entry guards; not revocation of arbitrary saved C pointers."""
from contextvars import ContextVar
from dataclasses import dataclass
import functools
import gc
import logging
import os
from pathlib import Path
import sys

from tests.postgres.errors import PgSafetyError

_allowed=ContextVar("paperpilot_pg_driver_scope",default=None)
_installed=False
_driver_connect=None
_driver_pgconn=None
_driver_generator=None
_real_env=os.path.normcase(os.path.realpath(Path(__file__).resolve().parents[2]/".env"))

def _deny(*args,**kwargs):
    raise PgSafetyError("PostgreSQL access requires the controlled test process")

def _audit(event,args):
    if event=="open" and args and isinstance(args[0],(str,bytes,os.PathLike)):
        if os.path.normcase(os.path.realpath(os.fsdecode(args[0])))==_real_env:
            raise PermissionError("Application environment access is forbidden in tests")

@dataclass(repr=False)
class _DriverScope:
    engine: object
    state: object
    parameters: dict

def _scope():
    from tests.postgres import policy
    value=_allowed.get()
    if not isinstance(value,_DriverScope) or policy.require_engine(value.engine,live=True) is not value.state:
        return _deny()
    return value

def _check_conninfo(conninfo):
    approved=_scope().parameters
    from psycopg.conninfo import conninfo_to_dict
    try:params=conninfo_to_dict(conninfo)
    except Exception:return _deny()
    if any(params.get(k)!=str(v) for k,v in approved.items()) or "service" in params:
        return _deny()

def _install_log_redaction():
    # Dedicated test-process policy. Sanitize before handlers or queues receive
    # library records; a final exception wrapper is too late for internal logs.
    original = logging.getLogRecordFactory()
    def redacted(*args, **kwargs):
        record = original(*args, **kwargs)
        if record.name in ("psycopg","sqlalchemy") or record.name.startswith(("psycopg.", "sqlalchemy.")):
            record.msg = "Database test diagnostic suppressed"
            record.args = ()
            record.exc_info = None
            record.exc_text = None
            record.stack_info = None
        return record
    logging.setLogRecordFactory(redacted)

def _install_sqlalchemy():
    from sqlalchemy import event
    from sqlalchemy.engine import Engine,Connection,NestedTransaction,TwoPhaseTransaction
    from tests.postgres import policy
    from tests.postgres.dbapi import acquisition,require_acquisition,execution
    def is_pg(engine):
        return engine.dialect.name=="postgresql"
    def engine_method(name):
        original=getattr(Engine,name)
        @functools.wraps(original)
        def guarded(self,*args,**kwargs):
            if is_pg(self):
                policy.require_engine(self)
                if name=="raw_connection":
                    require_acquisition(self)
                else:
                    with acquisition(self):
                        return original(self,*args,**kwargs)
            return original(self,*args,**kwargs)
        setattr(Engine,name,guarded)
    for name in ("connect","raw_connection"):engine_method(name)
    for name in ("execute","exec_driver_sql"):
        original=getattr(Connection,name)
        def wrap(original):
            @functools.wraps(original)
            def guarded(self,*args,**kwargs):
                if is_pg(self.engine):
                    statement=args[0] if args else kwargs.get("statement")
                    policy.require_statement(self,statement)
                    with execution(self):
                        return original(self,*args,**kwargs)
                return original(self,*args,**kwargs)
            return guarded
        setattr(Connection,name,wrap(original))
    @event.listens_for(Engine,"commit")
    def before_commit(connection):
        if is_pg(connection.engine):
            policy.require_connection(connection)
    def no_twophase(connection,*args,**kwargs):
        if is_pg(connection.engine):
            from tests.postgres.errors import PgSafetyError
            raise PgSafetyError("Two-phase transactions are unsupported by PostgreSQL test Engines")
    for name in ("begin_twophase","prepare_twophase","commit_twophase"):
        event.listen(Engine,name,no_twophase)
    original_twophase_commit=TwoPhaseTransaction.commit
    @functools.wraps(original_twophase_commit)
    def twophase_commit(self,*args,**kwargs):
        if is_pg(self.connection.engine):
            from tests.postgres.cleanup import reject_twophase
            reject_twophase(self.connection,self.xid)
        return original_twophase_commit(self,*args,**kwargs)
    TwoPhaseTransaction.commit=twophase_commit
    for name in ("rollback","close"):
        original=getattr(NestedTransaction,name)
        def wrap_nested(original):
            @functools.wraps(original)
            def nested(self,*args,**kwargs):
                if is_pg(self.connection.engine) and self.is_active:
                    from tests.postgres.cleanup import savepoint
                    failed=False
                    with savepoint(self):
                        try:
                            return original(self,*args,**kwargs)
                        except Exception:
                            failed=True
                    if failed:
                        policy.trip(self.connection.engine)
                        try:self.connection.invalidate()
                        except Exception:logging.getLogger(__name__).warning("SAVEPOINT cleanup invalidation failed")
                        from tests.postgres.errors import PgSafetyError
                        raise PgSafetyError("PostgreSQL SAVEPOINT rollback failed; checkout invalidated")
                return original(self,*args,**kwargs)
            return nested
        setattr(NestedTransaction,name,wrap_nested(original))
    for name in ("close","invalidate","detach"):
        original=getattr(Connection,name)
        def wrap(original):
            @functools.wraps(original)
            def cleanup(self,*args,**kwargs):
                if is_pg(self.engine):
                    from tests.postgres.safety import revoke
                    revoke(self)
                return original(self,*args,**kwargs)
            return cleanup
        setattr(Connection,name,wrap(original))
    original_dispose=Engine.dispose
    @functools.wraps(original_dispose)
    def dispose(self,*args,**kwargs):
        if is_pg(self):policy.finish(self)
        return original_dispose(self,*args,**kwargs)
    Engine.dispose=dispose

def reject_preexisting_engines():
    from sqlalchemy.engine import Engine
    from tests.postgres import policy
    for candidate in gc.get_objects():
        if Engine in type(candidate).__mro__ and candidate.dialect.name=="postgresql" and policy.state(candidate) is None:
            raise PgSafetyError("Pre-existing PostgreSQL Engine state requires a fresh process")

def install_guards():
    global _installed,_driver_connect,_driver_pgconn,_driver_generator
    if _installed:return
    _install_log_redaction()
    sys.addaudithook(_audit)
    import psycopg
    from psycopg import pq,generators
    _driver_connect=psycopg.Connection.connect.__func__
    _driver_pgconn=pq.PGconn
    _driver_generator=generators.connect
    native=_driver_pgconn
    generator=_driver_generator
    def sync_connect(cls,*args,**kwargs):
        approved=_scope().parameters
        if args or kwargs!=approved:return _deny()
        return _driver_connect(cls,*args,**kwargs)
    async def async_connect(cls,*args,**kwargs):
        return _deny()
    class Meta(type):
        def __getattr__(cls,name):
            value=getattr(_driver_pgconn,name)
            if name in ("connect","connect_start"):
                def guarded(*args,**kwargs):
                    _scope()
                    if len(args)!=1 or kwargs:return _deny()
                    _check_conninfo(args[0].decode())
                    return value(*args,**kwargs)
                return guarded
            return value
    class GuardedPGconn(metaclass=Meta):
        def __new__(cls,*args,**kwargs):return _deny()
    def guarded_generator(conninfo):
        _check_conninfo(conninfo)
        return _driver_generator(conninfo)
    # Cover current binary/Python exports, not just psycopg.pq.
    for name,module in tuple(sys.modules.items()):
        if module is not None and name.startswith("psycopg"):
            if getattr(module,"PGconn",None) is native:module.PGconn=GuardedPGconn
            if getattr(module,"connect",None) is generator:module.connect=guarded_generator
    generators.connect=guarded_generator
    psycopg.Connection.connect=classmethod(sync_connect)
    psycopg.connect=psycopg.Connection.connect
    psycopg.AsyncConnection.connect=classmethod(async_connect)
    pq.PGconn=GuardedPGconn
    _install_sqlalchemy()
    _installed=True

def _call_redacted(connector):
    failed=False
    try:return connector()
    except Exception:failed=True
    if failed:
        raise PgSafetyError("Test PostgreSQL connection failed; details suppressed")

def open_driver_connection(settings,opt_in,*,engine=None):
    from tests.postgres import policy
    from tests.postgres.settings import preflight
    state=policy.require_engine(engine,live=True)
    if state.settings is not settings:
        return _deny()
    preflight(settings,opt_in,fault=state.fault)
    import psycopg
    params=dict(host=settings.host,hostaddr=settings.host,port=settings.port,
                dbname=settings.db_name,user=settings.user,
                password=settings.password.get_secret_value(),connect_timeout=5,
                application_name="paperpilot-pg-tests",
                options="-c search_path=public -c lock_timeout=5s -c statement_timeout=15s")
    token=_allowed.set(_DriverScope(engine,state,params))
    try:return _call_redacted(lambda:psycopg.Connection.connect(**params))
    finally:
        _allowed.reset(token)
        params.clear()
