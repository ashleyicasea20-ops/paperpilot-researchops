"""Offline metadata/DDL checks and isolated SQLite constraint tests.

Only the explicit in-memory engine below may create model tables. PostgreSQL
is never used here; its only integration query remains the existing SELECT 1.
"""

from datetime import datetime, timezone
import subprocess
import sys
from uuid import UUID, uuid4

import pytest
from sqlalchemy import (
    Boolean, CheckConstraint, DateTime, String, UniqueConstraint, Uuid,
    create_engine, delete, event, inspect, select, text,
)
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, configure_mappers
from sqlalchemy.schema import CreateIndex, CreateTable

from app.db.base import Base
from app.models import User, Workspace, WorkspaceMember


@pytest.fixture
def session():
    engine = create_engine("sqlite+pysqlite:///:memory:", hide_parameters=True)

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(connection, _record):
        connection.execute("PRAGMA foreign_keys=ON")

    # Deliberately local engine: never substitute the application's get_engine().
    Base.metadata.create_all(engine)
    with Session(engine) as db_session:
        assert db_session.scalar(text("PRAGMA foreign_keys")) == 1
        yield db_session
    engine.dispose()


@pytest.fixture
def collaboration(session):
    owner = User(email="owner@example.test", password_hash="test-only-placeholder")
    member = User(email="member@example.test", password_hash="test-only-placeholder")
    workspace = Workspace(name="Research", owner=owner)
    session.add_all([workspace, member])
    session.flush()
    return owner, member, workspace


def test_all_models_share_complete_metadata():
    assert set(Base.metadata.tables) == {"users", "workspaces", "workspace_members"}
    for model in (User, Workspace, WorkspaceMember):
        assert model.__table__.metadata is Base.metadata
        assert all(constraint.name for constraint in model.__table__.constraints)


def test_primary_and_foreign_keys_use_python_uuids():
    for table, column in (
        (User.__table__, "id"), (Workspace.__table__, "id"),
        (Workspace.__table__, "owner_id"),
        (WorkspaceMember.__table__, "workspace_id"),
        (WorkspaceMember.__table__, "user_id"),
    ):
        assert isinstance(table.c[column].type, Uuid)
        assert table.c[column].type.as_uuid
        assert table.c[column].type.python_type is UUID
    assert list(User.__table__.primary_key.columns.keys()) == ["id"]
    assert list(Workspace.__table__.primary_key.columns.keys()) == ["id"]
    assert list(WorkspaceMember.__table__.primary_key.columns.keys()) == [
        "workspace_id", "user_id"
    ]
    assert "id" not in WorkspaceMember.__table__.c


def test_field_lengths_non_null_and_timestamp_metadata():
    for model in (User, Workspace, WorkspaceMember):
        assert all(not column.nullable for column in model.__table__.columns)
    for column, length in (
        (User.__table__.c.email, 320), (User.__table__.c.password_hash, 255),
        (Workspace.__table__.c.name, 150), (WorkspaceMember.__table__.c.role, 16),
    ):
        assert isinstance(column.type, String)
        assert column.type.length == length
    assert isinstance(User.__table__.c.is_active.type, Boolean)
    for model, names in (
        (User, ("created_at", "updated_at")),
        (Workspace, ("created_at", "updated_at")),
        (WorkspaceMember, ("joined_at",)),
    ):
        for name in names:
            column = model.__table__.c[name]
            assert isinstance(column.type, DateTime)
            assert column.type.timezone
            assert column.server_default is not None
        if "updated_at" in names:
            assert model.__table__.c.updated_at.onupdate is not None


def test_uuids_defaults_and_repr(session):
    first = User(email="first@example.test", password_hash="test-only-placeholder")
    second = User(email="second@example.test", password_hash="test-only-placeholder")
    session.add_all([first, second])
    session.flush()
    assert isinstance(first.id, UUID)
    assert first.id != second.id
    assert first.is_active is True
    assert "test-only-placeholder" not in repr(first)


def test_email_uniqueness_ignores_ascii_case(session):
    session.execute(User.__table__.insert().values(
        email="Researcher@Example.test", password_hash="test-only-placeholder"
    ))
    with pytest.raises(IntegrityError):
        session.execute(User.__table__.insert().values(
            email="researcher@example.test", password_hash="test-only-placeholder"
        ))


@pytest.mark.parametrize("field", ["email", "password_hash"])
@pytest.mark.parametrize("blank", ["", "   ", "\t\n\r\v\f", "\u00a0\u3000"])
def test_blank_user_values_are_rejected(session, field, blank):
    values = {"email": "valid@example.test", "password_hash": "test-only-placeholder"}
    values[field] = blank
    with pytest.raises(IntegrityError):
        session.execute(User.__table__.insert().values(**values))


@pytest.mark.parametrize("field", ["email", "password_hash", "is_active", "created_at", "updated_at"])
def test_required_user_values_reject_null(session, field):
    values = {"email": "valid@example.test", "password_hash": "test-only-placeholder"}
    values[field] = None
    with pytest.raises(IntegrityError):
        session.execute(User.__table__.insert().values(**values))


@pytest.mark.parametrize("blank", ["", "   ", "\t\r\n", "\u00a0\u3000"])
def test_blank_workspace_name_is_rejected(session, collaboration, blank):
    owner, _, _ = collaboration
    with pytest.raises(IntegrityError):
        session.execute(Workspace.__table__.insert().values(owner_id=owner.id, name=blank))


def test_workspace_owner_is_required_and_must_exist(session):
    with pytest.raises(IntegrityError):
        session.execute(Workspace.__table__.insert().values(owner_id=uuid4(), name="Research"))
    session.rollback()
    with pytest.raises(IntegrityError):
        session.execute(Workspace.__table__.insert().values(owner_id=None, name="Research"))


def test_owner_can_have_multiple_same_named_workspaces_without_membership(session, collaboration):
    owner, _, workspace = collaboration
    other = Workspace(name=workspace.name, owner=owner)
    session.add(other)
    session.flush()
    assert len(owner.owned_workspaces) == 2
    assert owner.workspace_memberships == []
    assert workspace.memberships == []


def test_duplicate_membership_is_rejected(session, collaboration):
    _, member, workspace = collaboration
    values = {"workspace_id": workspace.id, "user_id": member.id}
    session.execute(WorkspaceMember.__table__.insert().values(**values))
    with pytest.raises(IntegrityError):
        session.execute(WorkspaceMember.__table__.insert().values(**values))


@pytest.mark.parametrize("role", ["owner", "ADMIN", "manager", "", "viewer "])
def test_invalid_membership_role_is_rejected(session, collaboration, role):
    _, member, workspace = collaboration
    with pytest.raises(IntegrityError):
        session.execute(WorkspaceMember.__table__.insert().values(
            workspace_id=workspace.id, user_id=member.id, role=role
        ))


@pytest.mark.parametrize("role", ["admin", "editor", "viewer"])
def test_valid_member_roles_and_relationships(session, collaboration, role):
    owner, member, workspace = collaboration
    membership = WorkspaceMember(user=member, workspace=workspace, role=role)
    session.add(membership)
    session.flush()
    assert membership.workspace.owner is owner
    assert membership in member.workspace_memberships
    assert membership in workspace.memberships
    assert membership.joined_at is not None


def test_python_and_server_membership_defaults(session, collaboration):
    owner, member, workspace = collaboration
    membership = WorkspaceMember(user=member, workspace=workspace)
    session.add(membership)
    session.flush()
    assert membership.role == "viewer"
    # Raw SQL bypasses Python column defaults; UUIDs are SQLite's hex storage.
    other_id = uuid4()
    session.execute(text(
        "INSERT INTO users (id, email, password_hash) "
        "VALUES (:id, 'server-default@example.test', 'test-only-placeholder')"
    ), {"id": other_id.hex})
    session.execute(text(
        "INSERT INTO workspace_members (workspace_id, user_id) VALUES (:workspace, :user)"
    ), {"workspace": workspace.id.hex, "user": other_id.hex})
    session.expire_all()
    server_member = session.get(WorkspaceMember, (workspace.id, other_id))
    assert server_member.role == "viewer"
    assert server_member.joined_at is not None
    assert session.get(User, other_id).is_active is True
    assert owner.id == workspace.owner_id


@pytest.mark.parametrize("missing", ["user", "workspace"])
def test_membership_foreign_keys_are_enforced(session, collaboration, missing):
    _, member, workspace = collaboration
    with pytest.raises(IntegrityError):
        session.execute(WorkspaceMember.__table__.insert().values(
            workspace_id=uuid4() if missing == "workspace" else workspace.id,
            user_id=uuid4() if missing == "user" else member.id,
        ))


@pytest.mark.parametrize("loaded", [False, True])
def test_orm_owner_delete_is_restricted_for_loaded_and_unloaded_relationships(session, collaboration, loaded):
    owner, _, workspace = collaboration
    session.commit()
    if loaded:
        assert workspace in owner.owned_workspaces
    session.delete(owner)
    with pytest.raises(IntegrityError):
        session.flush()
    session.rollback()
    assert session.get(Workspace, workspace.id) is not None


def test_direct_owner_delete_is_restricted(session, collaboration):
    owner, _, _ = collaboration
    with pytest.raises(IntegrityError):
        session.execute(delete(User).where(User.id == owner.id))


@pytest.mark.parametrize("target", ["workspace", "member"])
@pytest.mark.parametrize("via_orm", [False, True])
def test_membership_delete_cascades(session, collaboration, target, via_orm):
    owner, member, workspace = collaboration
    session.add(WorkspaceMember(user=member, workspace=workspace))
    session.commit()
    if via_orm:
        session.delete(workspace if target == "workspace" else member)
    elif target == "workspace":
        session.execute(delete(Workspace).where(Workspace.id == workspace.id))
    else:
        session.execute(delete(User).where(User.id == member.id))
    session.flush()
    assert session.scalars(select(WorkspaceMember)).all() == []
    assert session.get(User, owner.id) is not None


@pytest.mark.parametrize("model", [User, Workspace])
def test_insert_timestamps_and_orm_update(session, collaboration, model):
    owner, _, workspace = collaboration
    entity = owner if model is User else workspace
    assert entity.created_at is not None
    assert entity.updated_at is not None
    old = datetime(2001, 1, 1, tzinfo=timezone.utc)
    entity.updated_at = old
    session.flush()
    created = entity.created_at
    if model is User:
        entity.is_active = False
    else:
        entity.name = "Updated research"
    session.flush()
    session.refresh(entity)
    assert entity.updated_at.replace(tzinfo=timezone.utc) > old
    assert entity.created_at == created


@pytest.mark.parametrize("model", [User, Workspace])
def test_raw_sql_does_not_automatically_update_timestamp(session, collaboration, model):
    owner, _, workspace = collaboration
    entity = owner if model is User else workspace
    entity.updated_at = datetime(2001, 1, 1)
    session.flush()
    session.refresh(entity)
    old = entity.updated_at
    statement = (
        "UPDATE users SET is_active = 0 WHERE id = :id"
        if model is User else "UPDATE workspaces SET name = 'Raw update' WHERE id = :id"
    )
    session.execute(text(statement), {"id": entity.id.hex})
    session.refresh(entity)
    assert entity.updated_at == old


def test_foreign_key_delete_definitions():
    expected = {
        ("workspaces", "owner_id"): ("users.id", "RESTRICT"),
        ("workspace_members", "workspace_id"): ("workspaces.id", "CASCADE"),
        ("workspace_members", "user_id"): ("users.id", "CASCADE"),
    }
    for (table, column), definition in expected.items():
        foreign_key, = Base.metadata.tables[table].c[column].foreign_keys
        assert (foreign_key.target_fullname, foreign_key.ondelete) == definition


def test_indexes_are_minimal_and_email_uses_one_expression_index():
    dialect = postgresql.dialect()
    user_index, = User.__table__.indexes
    assert user_index.unique
    assert str(user_index.expressions[0].compile(dialect=dialect)) == "lower(users.email)"
    assert not any(isinstance(c, UniqueConstraint) for c in User.__table__.constraints)
    owner_index, = Workspace.__table__.indexes
    member_index, = WorkspaceMember.__table__.indexes
    assert list(owner_index.columns.keys()) == ["owner_id"]
    assert list(member_index.columns.keys()) == ["user_id"]
    assert not owner_index.unique
    assert not member_index.unique


def test_postgresql_ddl_compiles_with_native_types_and_named_constraints():
    dialect = postgresql.dialect()
    for table in Base.metadata.sorted_tables:
        ddl = str(CreateTable(table).compile(dialect=dialect))
        assert "UUID" in ddl
        assert "TIMESTAMP WITH TIME ZONE" in ddl
        assert "DEFAULT now()" in ddl
        assert "PRIMARY KEY" in ddl
        for constraint in table.constraints:
            assert constraint.name in ddl
            if isinstance(constraint, CheckConstraint):
                assert "CHECK" in ddl
        for index in table.indexes:
            assert "CREATE" in str(CreateIndex(index).compile(dialect=dialect))
    email_ddl = str(CreateIndex(next(iter(User.__table__.indexes))).compile(dialect=dialect))
    assert "CREATE UNIQUE INDEX uq_users_email_lower ON users (lower(email))" == email_ddl


def test_relationships_resolve_without_overlaps_or_warnings():
    configure_mappers()
    for model, pairs in (
        (User, {"owned_workspaces": "owner", "workspace_memberships": "user"}),
        (Workspace, {"owner": "owned_workspaces", "memberships": "workspace"}),
        (WorkspaceMember, {"user": "workspace_memberships", "workspace": "memberships"}),
    ):
        mapper = inspect(model)
        assert set(mapper.relationships.keys()) == set(pairs)
        for name, back in pairs.items():
            assert mapper.relationships[name].back_populates == back


@pytest.mark.parametrize("module", ["user", "workspace", "workspace_member"])
def test_model_import_does_not_load_settings_or_connect(module):
    code = f"""
from unittest.mock import patch
from sqlalchemy.orm import configure_mappers
with patch('sqlalchemy.create_engine', side_effect=AssertionError('engine created')), \
     patch('pydantic_settings.BaseSettings.__init__', side_effect=AssertionError('settings loaded')):
    __import__('app.models.{module}')
    from app.db.base import Base
    configure_mappers()
    assert len(Base.metadata.tables) == 3
"""
    completed = subprocess.run([sys.executable, "-W", "error", "-c", code], capture_output=True, text=True)
    assert completed.returncode == 0, completed.stderr
