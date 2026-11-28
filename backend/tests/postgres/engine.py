"""Live and synthetic Engines use the same lease/fuse policy, never the same permit."""
from contextlib import contextmanager
from hashlib import sha256
from sqlalchemy import create_engine,event
from sqlalchemy.engine import Engine
from sqlalchemy.engine.default import DefaultDialect
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import QueuePool
from tests.postgres import policy,runtime
from tests.postgres.guards import install_guards,open_driver_connection
from tests.postgres.safety import _TICKET,require_verified,verify_connection
from tests.postgres.settings import PgSafetyError,preflight

def _attach(engine,settings,intent,*,mode,owner=None,fault=False):
    record=policy.register(engine,mode=mode,settings=settings,owner=owner,fault=fault)
    from tests.postgres import dbapi,cleanup
    from sqlalchemy.engine import Connection
    dbapi.attach_pool(engine)
    def no_twophase(*args,**kwargs):
        connection=args[0] if args and isinstance(args[0],Connection) else None
        xid=args[1] if len(args)>1 else None
        cleanup.reject_twophase(connection,xid)
    for name in ("do_begin_twophase","do_prepare_twophase","do_commit_twophase"):
        setattr(engine.dialect,name,no_twophase)
    rollback_twophase=engine.dialect.do_rollback_twophase
    def controlled_twophase_rollback(connection,xid,is_prepared=True,recover=False):
        failed=False
        with cleanup.twophase_rollback(connection,xid,recover=recover):
            try:
                return rollback_twophase(connection,xid,is_prepared=is_prepared,recover=recover)
            except Exception:
                failed=True
        if failed:
            policy.trip(engine)
            try:connection.invalidate()
            except Exception:pass
            raise PgSafetyError("Legacy two-phase rollback failed; checkout invalidated")
    engine.dialect.do_rollback_twophase=controlled_twophase_rollback
    initialize=engine.dialect.initialize
    def initialize_dialect(connection):
        policy._origin(connection,engine)
        token=policy._initializing.set((engine,id(connection),id(connection.connection.driver_connection)))
        failed=False
        try:
            initialize(connection)
        except Exception:
            policy.trip(engine)
            failed=True
        finally:
            policy._initializing.reset(token)
        if failed:
            raise PgSafetyError("PostgreSQL dialect initialization failed; Engine stopped")
    engine.dialect.initialize=initialize_dialect
    @event.listens_for(engine,"engine_connect")
    def attest(connection):
        try:
            policy.require_engine(engine)
            if isinstance(connection,Connection):dbapi.bind(connection)
            verify_connection(connection,settings,intent)
            if isinstance(connection,Connection):dbapi.confirmed(connection)
            record.attested=True
        except Exception:
            policy.trip(engine)
            raise
    @event.listens_for(engine,"before_cursor_execute")
    def check(connection,cursor,statement,parameters,context,executemany):
        policy.require_statement(connection,statement,engine,context=context,parameters=parameters)
        if isinstance(connection,Connection):dbapi.approve_verification_statement(connection,statement,context)
    @event.listens_for(engine,"checkin")
    def expire(dbapi_connection,pool_record):
        pool_record.info.pop(_TICKET,None)
    return engine

def create_test_engine(settings,opt_in,*,fault=False):
    preflight(settings,opt_in,fault=fault)
    owner=runtime.permission(fault=fault)
    install_guards()
    def creator():
        policy.require_engine(engine,live=True)
        failed=False
        try:
            return open_driver_connection(settings,opt_in,engine=engine)
        except Exception:
            policy.trip(engine)
            failed=True
        if failed:
            raise PgSafetyError("PostgreSQL test connection failed; Engine stopped")
    engine=create_engine(settings.database_url,creator=creator,isolation_level="READ COMMITTED",
                         hide_parameters=True,echo=False,use_native_hstore=False,pool_size=4,max_overflow=0,
                         pool_timeout=5,pool_pre_ping=False)
    return _attach(engine,settings,opt_in,mode="live",owner=owner,fault=fault)

def create_offline_test_engine(settings,opt_in):
    """Built-in synthetic DBAPI only: this function cannot issue driver permission."""
    preflight(settings,opt_in)
    install_guards()
    import psycopg
    from tests.postgres._synthetic import Lab
    lab=Lab()
    # SQLAlchemy uses the module's Error class even when an offline guard
    # raises before executing on the synthetic cursor.
    dialect=DefaultDialect(dbapi=psycopg, paramstyle="named")
    dialect.name="postgresql"
    dialect.driver="synthetic"
    engine=Engine(QueuePool(lab.connect,pool_size=4,max_overflow=0),dialect,
                  settings.database_url,hide_parameters=True)
    engine._review_lab=lab
    return _attach(engine,settings,opt_in,mode="synthetic")

def require_test_engine(engine):
    return policy.require_engine(engine,live=True)

def verified_session_factory(engine):
    record=policy.require_engine(engine)
    if not record.attested:
        raise PgSafetyError("Server verification must finish before Session injection")
    return sessionmaker(bind=engine,autoflush=False,expire_on_commit=False)

def synthetic_auth_settings():
    from app.core.config import AuthSettings
    return AuthSettings(jwt_secret_key=sha256(b"PaperPilot isolated PostgreSQL test signing key").hexdigest(),_env_file=None)

@contextmanager
def inject_application(app,engine):
    from app.core.config import get_auth_settings
    from app.db import session as db
    require_test_engine(engine)
    factory=verified_session_factory(engine)
    auth=synthetic_auth_settings()
    old=db.get_session_factory
    missing=object()
    previous=app.dependency_overrides.get(get_auth_settings,missing)
    db.get_session_factory=lambda:factory
    app.dependency_overrides[get_auth_settings]=lambda:auth
    try:yield factory
    finally:
        db.get_session_factory=old
        if previous is missing:app.dependency_overrides.pop(get_auth_settings,None)
        else:app.dependency_overrides[get_auth_settings]=previous
