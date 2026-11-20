"""Create users, workspaces, and workspace_members.

Revision ID: 20261120_0001
Revises: None

Frozen Stage 02B schema. Do not import application models into historical
revisions. UUID and updated_at Python defaults are intentionally not server
defaults. Downgrade deletes these tables and their data.
"""

from alembic import op
import sqlalchemy as sa

revision: str = "20261120_0001"
down_revision = None
branch_labels = None
depends_on = None

# Frozen Unicode White_Space set used by this revision's nonblank checks.
WHITESPACE_CHARACTERS = (
    " \t\n\r\v\f\u0085\u00a0\u1680"
    "\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a"
    "\u2028\u2029\u202f\u205f\u3000"
)


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_users")),
        sa.CheckConstraint(
            sa.func.length(sa.func.trim(sa.column("email"), WHITESPACE_CHARACTERS)) > 0,
            name=op.f("ck_users_email_not_blank"),
        ),
        sa.CheckConstraint(
            sa.func.length(sa.func.trim(sa.column("password_hash"), WHITESPACE_CHARACTERS)) > 0,
            name=op.f("ck_users_password_hash_not_blank"),
        ),
    )
    op.create_index("uq_users_email_lower", "users", [sa.text("lower(email)")], unique=True)
    op.create_table(
        "workspaces",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("owner_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(150), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_workspaces")),
        sa.ForeignKeyConstraint(
            ["owner_id"], ["users.id"], ondelete="RESTRICT",
            name=op.f("fk_workspaces_owner_id_users"),
        ),
        sa.CheckConstraint(
            sa.func.length(sa.func.trim(sa.column("name"), WHITESPACE_CHARACTERS)) > 0,
            name=op.f("ck_workspaces_name_not_blank"),
        ),
    )
    op.create_index(op.f("ix_workspaces_owner_id"), "workspaces", ["owner_id"])
    op.create_table(
        "workspace_members",
        sa.Column("workspace_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("user_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("role", sa.String(16), server_default=sa.text("'viewer'"), nullable=False),
        sa.Column("joined_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("workspace_id", "user_id", name=op.f("pk_workspace_members")),
        sa.ForeignKeyConstraint(
            ["workspace_id"], ["workspaces.id"], ondelete="CASCADE",
            name=op.f("fk_workspace_members_workspace_id_workspaces"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], ondelete="CASCADE",
            name=op.f("fk_workspace_members_user_id_users"),
        ),
        sa.CheckConstraint(
            "role IN ('admin', 'editor', 'viewer')", name=op.f("ck_workspace_members_role_allowed"),
        ),
    )
    op.create_index(op.f("ix_workspace_members_user_id"), "workspace_members", ["user_id"])


def downgrade() -> None:
    op.drop_index(op.f("ix_workspace_members_user_id"), table_name="workspace_members")
    op.drop_table("workspace_members")
    op.drop_index(op.f("ix_workspaces_owner_id"), table_name="workspaces")
    op.drop_table("workspaces")
    op.drop_index("uq_users_email_lower", table_name="users")
    op.drop_table("users")
