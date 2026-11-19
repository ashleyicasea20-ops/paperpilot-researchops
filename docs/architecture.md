# PaperPilot ResearchOps Architecture

## Stage 02B scope

Stage 02A established synchronous PostgreSQL connectivity, configuration, session lifecycle management, and a readiness probe. Stage 02B adds the User, Workspace, and WorkspaceMember ORM definitions and offline schema tests. These definitions are not deployed tables: this stage does not create or change PostgreSQL schema. Alembic migrations belong to Stage 02C. Authentication, authorization APIs, queues, containers, caches, agent frameworks, and model providers remain outside this stage.

## Runtime topology

```text
Browser (localhost:3000)
  |
  | GET /api/v1/health
  | Origin: http://localhost:3000
  v
Next.js 16 application                FastAPI application
frontend/                             backend/app/
  - App Router                          - versioned /api/v1 routes
  - React client status widget          - explicit CORS allowlist
  - NEXT_PUBLIC_API_BASE_URL            - typed health response
          |                                      ^
          +----------- HTTP :8000 ---------------+
                                                 |
                                      GET /api/v1/ready
                                      SELECT 1 via SQLAlchemy
                                                 |
                                                 v
                                  PostgreSQL 17 (127.0.0.1:5432)
                                  database: paperpilot_dev
```

The homepage is mostly a React Server Component. Only the health-status widget is a Client Component because it needs browser lifecycle state and a cross-origin HTTP request.

## Repository boundaries

- `frontend/` owns the browser application, presentation, and public runtime configuration.
- `backend/app/` owns HTTP APIs and application behavior.
- `backend/app/core/config.py` validates environment-backed database settings and builds a safe SQLAlchemy URL.
- `backend/app/db/session.py` owns the synchronous engine, connection pool, session factory, and connectivity check.
- `backend/app/db/base.py` owns the shared declarative metadata and UUID/timestamp mixins.
- `backend/app/models/` owns identity, workspace ownership, and collaborator mappings.
- `backend/tests/` owns API contract and middleware checks.
- `docs/` records architecture decisions that apply across both runtimes.

The Python virtual environment and Node.js dependencies remain local build artifacts and are excluded from Git.

## API contract

`GET /api/v1/health` is a liveness check that does not depend on PostgreSQL:

```json
{
  "status": "ok",
  "service": "paperpilot-api",
  "version": "0.1.0"
}
```

`GET /api/v1/ready` executes `SELECT 1` against PostgreSQL. It returns HTTP 200 with `{"status":"ok","database":"ready"}` when the database is available, or HTTP 503 with a generic message otherwise. Database exceptions and connection URLs are not exposed through the API.

The `/api/v1` prefix creates room for future non-breaking additions and an explicit migration path for breaking API changes.

## Configuration and security

- The frontend reads `NEXT_PUBLIC_API_BASE_URL`; the `NEXT_PUBLIC_` prefix means the value is intentionally visible in the browser bundle.
- The backend reads `CORS_ORIGINS` as a comma-separated allowlist and defaults to `http://localhost:3000` only.
- Pydantic Settings reads `DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USER`, and `DB_PASSWORD` from `backend/.env` or the process environment.
- `DB_PASSWORD` is represented as a Pydantic `SecretStr`. SQLAlchemy `URL.create` receives the raw value directly so reserved characters are handled without manual URL interpolation.
- Real `.env` files are ignored; `.env.example` contains placeholders only. Logs and API responses must not include credentials or full connection URLs.
- CORS is a browser access control, not authentication. Authentication and authorization must be designed before protected research data is introduced.

## Connection lifecycle

The engine and session factory are created lazily and cached once per process. This keeps `/api/v1/health` operational when database configuration is absent while `/api/v1/ready` accurately reports the database dependency. The engine uses `pool_pre_ping`, a five-connection base pool, up to ten overflow connections, a five-second pool wait, a five-second driver connection timeout, and periodic connection recycling.

Application sessions are request-scoped generators: successful use closes the session, exceptions trigger rollback, and the connection is always returned to the pool. Stage 02A exposes the session provider for later data access but does not create tables or persist domain data.

## Verification boundary

Readiness unit tests mock the connectivity function to exercise HTTP 200 and 503 contracts. A separately marked `integration` test uses `backend/.env` and the real PostgreSQL server; it is skipped with an explicit reason when local credentials are not configured. Mock tests are never reported as real database verification.

Integration failures report only the exception type and a generic diagnostic message. Driver/configuration exceptions are caught before calling `pytest.fail(..., pytrace=False)` outside the exception handler, preventing pytest from expanding credential-bearing driver frames or chained exception context. Unit regressions cover both driver and settings failures. Connection failures remain failures, rather than being silently skipped.

Stage 02B model tests use an isolated `sqlite+pysqlite:///:memory:` engine with `PRAGMA foreign_keys=ON`. Only that fixture creates tables with `Base.metadata.create_all(engine)`; it closes/disposes the in-memory database afterward. No application startup code, model import, or PostgreSQL integration test runs schema DDL. Model tests do not load local credentials. PostgreSQL DDL is compiled offline, including expression indexes and named constraints; the real integration test remains the existing read-only `SELECT 1`.

SQLite exercises basic constraints and ORM cascades, but it is not a PostgreSQL compatibility proof: its default `lower()` only handles ASCII case, `VARCHAR(n)` does not enforce PostgreSQL's length limit, UUIDs are stored as hex text, timezone-aware timestamp storage differs, and transaction/concurrency behavior differs. Native PostgreSQL schema and behavior tests will require a separately approved disposable database in a later stage.

## ORM schema and metadata

All three models use one `Base(DeclarativeBase)` and `Base.metadata`. Import `app.models` before supplying `Base.metadata` as Stage 02C's Alembic `target_metadata`; importing `Base` alone does not discover models. Model imports only register Python mappings and never create an engine or load database settings.

The metadata naming convention generates `pk_<table>`, `fk_<table>_<column>_<target>`, `ck_<table>_<check-name>`, `uq_<table>_<column>`, and `ix_<table>_<column>`. The email expression index has the explicit stable name `uq_users_email_lower`. This makes future migration output reproducible.

| Model / table | Columns (all NOT NULL) | Keys, checks, and secondary indexes |
| --- | --- | --- |
| User / `users` | `id Uuid`, `email String(320)`, `password_hash String(255)`, `is_active Boolean`, `created_at` / `updated_at DateTime(timezone=True)` | UUID PK; unique index on `lower(email)`; nonblank email/hash checks; active defaults to true |
| Workspace / `workspaces` | `id Uuid`, `owner_id Uuid`, `name String(150)`, `created_at` / `updated_at DateTime(timezone=True)` | UUID PK; owner FK to users with RESTRICT; owner index; nonblank name check; names are not unique |
| WorkspaceMember / `workspace_members` | `workspace_id Uuid`, `user_id Uuid`, `role String(16)`, `joined_at DateTime(timezone=True)` | Composite PK `(workspace_id, user_id)`; workspace/user FKs with CASCADE; user index; role CHECK allows admin/editor/viewer only; default viewer |

`Uuid(as_uuid=True)` maps Python `uuid.UUID` to native PostgreSQL UUID. A shared UUID mixin uses the callable `uuid4`, generating a fresh ID at SQLAlchemy insert time, not when constructing an unsaved object. There is no UUID server default, so direct SQL inserts must supply IDs. WorkspaceMember uses its two FKs as the primary key and has no redundant ID or workspace-only index; the composite primary key already supports workspace-prefix lookups.

The unique email index provides database protection against case variants according to PostgreSQL `lower()` and the database's locale/collation. It does not implement full Unicode case folding, Unicode normalization, whitespace stripping, or email syntax validation. Unicode case behavior may differ across collations and from Python `casefold()`; no universal Unicode-equivalence claim is made. Stage 03 must define and validate the input policy (including strip/lower and internationalized addresses) consistently before writes. Blank checks for email, password hash, and workspace name trim an explicit shared Unicode White_Space set; they reject empty/whitespace-only strings without modifying stored text. Invisible formatting characters outside that set, such as zero-width space, are not treated as whitespace. No password hashing service or authentication endpoint is implemented; `password_hash` stores only a future service-produced hash, and the default model representation does not expose column values.

## Ownership, relationships, and deletion

`Workspace.owner_id` is the unique authoritative ownership source. An owner does not need a membership row, and deleting or losing a membership cannot change ownership. `owner` is not a legal member role; the future authorization layer must always give the owner highest workspace permissions. WorkspaceMember records ordinary collaborators with admin/editor/viewer roles and defaults to viewer.

Relationships use explicit `back_populates`: User.owned_workspaces ↔ Workspace.owner, User.workspace_memberships ↔ WorkspaceMember.user, and Workspace.memberships ↔ WorkspaceMember.workspace. There are no overlapping direct many-to-many shortcuts. A user may own many workspaces, including several with the same name.

Deleting a user who still owns a workspace is rejected by the owner FK's `ON DELETE RESTRICT`. User.owned_workspaces uses `passive_deletes="all"` with no delete cascade so ORM deletion does not null out owner IDs or delete owned workspaces, even when that relationship is loaded. Workspace and ordinary user deletion remove associated membership rows using `ON DELETE CASCADE`; membership collections also use ORM delete-orphan cascade and passive deletion for unloaded collections.

Database constraints guarantee references, non-null/nonblank values, email uniqueness under the stated case rules, valid member roles, unique member pairs, and the deletion policies. They do not verify the caller, account activation, access to a supplied workspace ID, role-specific actions, ownership transfer, or whether an owner has also been inserted as an ordinary member. Stage 03 must prevent redundant owner membership in its service layer and authorize every workspace operation using the authenticated user plus `owner_id` **or** an effective membership. A workspace ID supplied by the frontend is never sufficient authority. No permission API, authentication middleware, or database row-level security is implemented in this stage.

## Timestamp behavior

User and Workspace share non-null `created_at` and `updated_at`; WorkspaceMember has non-null `joined_at`. All use `DateTime(timezone=True)` and database `now()` insert defaults. `updated_at` also has SQLAlchemy `onupdate=func.now()`, applied when an ORM (or SQLAlchemy Core) UPDATE omits an explicit updated_at value. No-op ORM flushes do not issue an UPDATE. Direct SQL bypasses this client-side rule and leaves updated_at unchanged unless the statement sets it. There is no update trigger or automatic database-side update rule; such a requirement would need a later migration. PostgreSQL `now()` is the transaction timestamp, so several updates in one transaction need not have strictly increasing timestamps.

## Intended growth path

Future capabilities should be added behind tested service boundaries rather than placed directly in the route module. Stage 02C can generate and review Alembic migrations from this metadata; Stage 03 can add input normalization, password hashing, authentication, and workspace authorization. Model definitions alone do not deploy schema or enforce request permissions.

