# PaperPilot ResearchOps Architecture

## Stage 02C status: local database infrastructure complete

Stage 01 completed the frontend/backend foundation. Stage 02A established synchronous PostgreSQL connectivity, Settings, session lifecycle management, and readiness. Stage 02B added User, Workspace, and WorkspaceMember ORM definitions. Stage 02C completed Alembic configuration and initial revision 20261120_0001 and recorded an approved local migration and isolated rehearsal. Stage 03A added registration, Stage 03B added login, Stage 03C-1 added identity/RBAC, and Stage 03C-2 added production Workspace CRUD. Stage 03C-3 adds member list/add/role-change/remove APIs using that same schema. Stages 03C-2/3 do not inspect or mutate the actual PostgreSQL environment. Ownership transfer, invitations/acceptance, mailbox verification and PDF/resource isolation remain future work.

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
- `backend/app/core/config.py` validates separate database and JWT settings and builds a safe SQLAlchemy URL.
- `backend/app/db/session.py` owns the synchronous engine, connection pool, session factory, and connectivity check.
- `backend/app/db/base.py` owns the shared declarative metadata and UUID/timestamp mixins.
- `backend/app/models/` owns identity, workspace ownership, and collaborator mappings.
- `backend/app/api/v1/auth.py` owns the registration route and its validation/dependency error boundaries.
- `backend/app/api/v1/login.py` owns the login and current-account routes and their separate error boundary.
- `backend/app/api/dependencies.py` provides verified JWT subjects, current identities, and workspace permission dependencies.
- `backend/app/api/protected_route.py` translates service errors into safe HTTP responses on opted-in routers.
- `backend/app/schemas/auth.py` defines shared email validation, authentication input, token output, and the public user response.
- `backend/app/core/security.py` owns password policy and reusable Argon2id hashing/verification primitives.
- `backend/app/core/token.py` issues and verifies fixed-policy JWT access tokens.
- `backend/app/core/authorization.py` owns the explicit role matrix and member-management policy.
- `backend/app/services/registration.py` owns user creation, commit/rollback, and precise duplicate classification.
- `backend/app/services/authentication.py` looks up stored users and verifies credentials through read-only queries.
- `backend/app/services/workspace_access.py` resolves account and workspace access through a caller-owned Session.
- `backend/alembic.ini` and `backend/alembic/` own migration configuration, the environment, the revision template, and immutable schema history.
- `backend/tests/` owns API, model, configuration, and offline migration checks.
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

### Stage 03A registration contract

`POST /api/v1/auth/register` accepts JSON email/password and returns 201 with only id, normalized email, is_active, created_at, and updated_at. A synthetic request/response example is in the README. It does not return passwords/hashes, tokens, cookies, or internal exception details. Registration does not establish a login session or verify mailbox ownership.

Error responses contain a generic detail: 409 for the recognized email unique-index conflict, 422 for invalid input, 503 for unavailable database/configuration, and 500 for unexpected internal failures. A registration-specific APIRoute wrapper sanitizes RequestValidationError before FastAPI can serialize input, ctx, or exception messages. An outer route boundary also covers dependency cleanup outside the inner handler; function-scoped Session cleanup occurs before the response. Errors are handled without recording raw exceptions. Health/readiness behavior is retained, and CORS adds POST to GET while preserving the Origin allowlist and disabled credentials.

Email input must be a string: trim surrounding whitespace, validate using EmailStr/email-validator without DNS checks, then lowercase and enforce ASCII plus the 320-character storage limit. The library's stricter syntax/length rules still apply. Both input and normalized output must be ASCII; display-name addresses and internationalized email are rejected in V1. Plus tags and dots remain intact. No provider-specific comparison rule is introduced; the database lower(email) index remains authoritative.

Passwords contain 15 through 128 Unicode code points and at most 1024 UTF-8 bytes. No trimming, case conversion, or composition rules are applied. With valid Unicode the 128-code-point cap is already stricter than the byte cap; both checks are retained. Invalid Unicode and non-string values are rejected before hashing. Request password fields use SecretStr and are excluded from repr. pwdlib[argon2] 0.4.2 uses PasswordHash.recommended() and Argon2id with library-managed random salt; email-validator is pinned at 2.4.0. No custom hash or encryption is implemented. The stored hash fits String(255), and the response schema has an explicit public field allowlist.

The synchronous route reuses get_db_session. The service hashes the validated password, adds the existing User model, flushes server-generated values, constructs the public response, and commits. Constructing the response before commit allows validation failures to roll back. Other failures attempt rollback; the existing dependency always attempts close. SQLAlchemy engine parameters are hidden, and application code does not log passwords, hashes, request bodies, or raw driver errors. External logging/APM must retain equivalent redaction.

There is no uniqueness prequery: concurrent insertion is arbitrated by uq_users_email_lower. PostgreSQL mapping requires both SQLSTATE 23505 and the exact diagnostic index name; SQLite tests recognize only its specific unique-index error code and message. Other integrity/programming errors produce 500, not 409. Database connection errors produce 503, including dependency setup failures. A network failure during commit can make the result uncertain; rollback cannot undo an already acknowledged server commit, so a later client retry may encounter 409. Registration idempotency, account recovery, and rate limiting are future work.

### Registration test safety

New tests use only an isolated SQLite in-memory Engine with foreign keys and dependency_overrides. Sessions and Engines are released, and PostgreSQL connection attempts are blocked in the API tests. Tests cover real Argon2id, input/response secrecy, real SQLite rollback, and simulated PostgreSQL diagnostics. They do not prove PostgreSQL two-connection race behavior. The existing real PostgreSQL integration test remains SELECT 1 only.

paperpilot_auth_test is a reserved future database name, not a created or configured database. No new PostgreSQL registration write test is enabled. Future write testing requires explicit approval, a separate database, an opt-in such as PAPERPILOT_AUTH_PG_TESTS=1, actual identity/schema/revision checks, and rejection of paperpilot_dev and paperpilot_migration_test. An outer Connection transaction with SAVEPOINT isolation (for example Session join_transaction_mode="create_savepoint") must allow service-level commit to be undone by the outer rollback. Concurrent two-connection behavior needs a separately approved test design. No schema change or migration belongs to Stage 03A.

### Stage 03B login and current-account contract

`POST /api/v1/auth/login` accepts JSON with `email` and `password`. It reuses registration's ASCII normalization and queries users through `lower(email)`, matching the existing unique expression index. Login accepts a nonempty string up to 1024 UTF-8 bytes, preserves every character, and verifies it against the stored Argon2id hash. A precomputed Argon2id hash supplies the same verification operation for unknown or disabled users. Database access and hash parameters can still cause timing differences.

Successful login returns HTTP 200 with `access_token`, `token_type` (`bearer`), and `expires_in` (`900`). PyJWT 2.15.1 signs with HS256 and a 32-byte key. Verification uses a fixed algorithm allowlist, issuer `paperpilot-researchops`, and exact audience `paperpilot-api`. Signed `sub`, `iat`, `nbf`, `exp`, `iss`, `aud`, and `token_type` are required. The subject must parse as a UUID, type must be `access`, and the integer timestamps must be in order and observe the 900-second lifetime. The issued claims contain account identity and token metadata. Passwords, hashes and credentials stay outside the token.

`GET /api/v1/auth/me` receives `Authorization: Bearer <ACCESS_TOKEN>`. A dependency verifies the JWT before opening a database Session. The service loads the current database record by `sub` and checks `is_active`, so disabling or removing the user rejects an existing token. A successful response contains only `id`, `email`, `is_active`, `created_at`, and `updated_at`. Both successful responses set `Cache-Control: no-store`.

Bad credentials return 401 with `Invalid credentials`. Unusable tokens, including tokens for disabled or missing users, return 401 with `Invalid access token`. Both responses add `WWW-Authenticate: Bearer`. Invalid login input returns 422 with `Invalid login input`. Missing or invalid signing configuration returns 503 with `Authentication is unavailable`; database connectivity failures return 503 with `Database is unavailable`. Unexpected failures, including internal parser errors and Session cleanup failures, return a generic 500 before headers are sent. Logs contain fixed messages without raw exceptions, passwords, complete tokens, or SQL parameters.

The routes use a distinct authentication request boundary and share FastAPI body-error recognition and database outage classification with registration. Login copies the ID, active flag and password hash from a single read, then closes the Session before Argon2 verification or token issuance. Account state can change between that read and issuance. `/me` checks the current account on every request, requires one unambiguous `Authorization` header, and builds the public response within its own read-only Session. Both endpoints finish cleanup before returning success. On failure the Session attempts rollback and close, with invalidation as a fallback if close fails. Cleanup errors are logged with fixed messages and the primary rejection or database error is preserved. Invalidation is a best-effort fallback. Registration retains its separate write transaction and post-commit cleanup behavior.

### Stage 03B verification and deployment boundaries

Authentication API tests use synthetic accounts, an isolated in-memory SQLite database, and HTTPX's ASGI transport. They block PostgreSQL connection attempts and use an independent test key. Tests cover real Argon2id verification, signed JWT validation, a full register-login-current-account flow, and error simulations. JWT unit tests construct expired or future claims without sleeping. PostgreSQL authentication with a prepared test user needs a separately authorized verification.

Initial Stage 03B verification passed 240 non-integration tests, including 77 new authentication tests. The first security-fix regression passed 257 non-integration tests. The existing PostgreSQL `SELECT 1` test was excluded from both runs. `pip check` passed in both runs.

Refresh tokens, OAuth, logout revocation, mailbox verification, password recovery, workspace authorization, and tenant isolation are separate features. Before public deployment, configure HTTPS, request size limits, rate limits, Argon2 concurrency limits, and redaction in SQLAlchemy pool logs and at the proxy and APM layers. Use separately generated keys per environment and document key rotation and incident response. Replacing the current key invalidates tokens signed by the previous key after the new configuration is loaded.

The remaining Stage 03B follow-up covers the classification of invalid Argon2 work parameters in stored hashes. Stage 03C-1 does not change password verification.

### Stage 03C-1 workspace authorization

Authentication establishes the current account. `TokenSubject` validates a single Bearer credential and extracts the signed UUID. `CurrentIdentity` additionally queries the active account and returns an immutable `AuthenticatedIdentity` containing only `user_id`. `/me` shares the token dependency and checks the active account while building the public profile.

Authorization grants a specific operation for one workspace. `workspace_permission(Permission.WORKSPACE_READ)` can protect a route on `APIRouter(route_class=ProtectedRoute)` with a `workspace_id` path parameter. The dependency checks the current account and workspace within the same read Session, finishes cleanup, and returns an immutable `WorkspaceAccessContext` with `workspace_id`, `owner_id`, `user_id` and `effective_role`. The required permission is selected by the route. The subject comes from the verified Token, and a membership is selected by both workspace and user ID. Ownership uses `Workspace.owner_id` before membership lookup, including when the owner also has a membership row. Unknown membership roles fail closed. Contexts are server-side values with request or transaction scope. Role checks read the database on each request.

| Permission | Owner | Admin | Editor | Viewer |
| --- | --- | --- | --- | --- |
| `workspace:read`, `resource:read`, `member:read` | Yes | Yes | Yes | Yes |
| `resource:create`, `resource:update` | Yes | Yes | Yes | — |
| `workspace:update`, `resource:delete`, `member:manage_basic` | Yes | Yes | — | — |
| `workspace:delete`, `workspace:transfer`, `member:manage_admin` | Yes | — | — | — |

`can_manage_member` checks the actual target ID, the stored role and proposed role. Owners may manage admin, editor and viewer members. Admins may add, update or remove editor and viewer members, with both roles required to satisfy the policy. The predicate rejects operations targeting the owner or the acting user, and rejects `owner` or unrecognized values as either ordinary member role. A missing role represents an absent membership or a removal, according to the operation. The caller must look up actual stored roles when using this policy. Resource and transfer permissions provide future policy entries. Workspace CRUD, membership writes, ownership transfer, and PDF isolation are outside this stage.

`ProtectedRoute` supplies route-scoped, non-cacheable responses. Invalid authentication returns 401 with `WWW-Authenticate: Bearer`. An absent workspace and an inaccessible workspace both return 404 with `Workspace not found`. A user who has workspace access but lacks the operation permission receives 403. Database outages return 503, and unexpected failures return a sanitized 500. An authorization result is returned only after successful cleanup. An already determined rejection survives a cleanup fault under the existing read-session rules. Registration retains its own write-transaction and error handling.

For written changes, `require_workspace_permission(session, subject, workspace_id, permission)` checks the active account and current authorization using the supplied business transaction. Pass the verified `TokenSubject`; the caller owns Session lifetime and commit. Authorization queries suppress autoflush. A standalone read dependency gives a point-in-time result, and account, ownership and membership can change afterward. Future writers must recheck within their business transaction and choose locking or conditional writes appropriate to the mutation. Stage 03C-1 provides neither transaction-level revocation guarantees nor workspace-scoped conditions for future resource queries.

Tests run permission and member-policy checks in Python and exercise authorization through a separate FastAPI app, in-memory SQLite and synthetic JWTs. They cover cross-workspace role differences, immediate role changes on subsequent requests, input spoofing, fail-closed role handling and Session errors. Stage 03C-1 passed 389 non-integration tests, including 132 new cases. The existing PostgreSQL integration test was excluded, and the authentication fixture blocks PostgreSQL connections. Native PostgreSQL transaction and cross-user concurrency tests require separate authorization.

### Stage 03C-2 production Workspace APIs

`backend/app/api/v1/workspaces.py` registers five production operations through a small ProtectedRoute subclass. `TokenSubject` verifies the credential; the service rechecks the active account and authorization rather than trusting client user IDs, JWT roles or a previous read dependency. The existing role matrix is unchanged.

| Endpoint | Authorization | Response |
| --- | --- | --- |
| POST `/api/v1/workspaces` | Active account; server assigns ownership | 201 WorkspaceResponse |
| GET `/api/v1/workspaces` | Active account; SQL-filtered owner/valid memberships | 200 WorkspaceListResponse |
| GET `/api/v1/workspaces/{workspace_id}` | workspace:read | 200 WorkspaceResponse |
| PATCH `/api/v1/workspaces/{workspace_id}` | workspace:update; owner/admin | 200 WorkspaceResponse |
| DELETE `/api/v1/workspaces/{workspace_id}` | workspace:delete; owner only | 204, empty body |

Create/update accept exactly one required strict string field `name`, length 1–150. Unicode whitespace-only strings, NUL, malformed Unicode, unknown fields and empty PATCH bodies are rejected. Valid whitespace around a nonblank name is preserved. Output is constructed from an explicit whitelist: id, name, owner_id, created_at, updated_at, effective_role. No ORM entity, password/hash, member profile or credential is serialized. Creating a workspace does not insert an owner membership.

Scoped reads outer-join only the caller's membership and filter ownership OR admin/editor/viewer in SQL. The composite membership key prevents duplicate joins; owner role takes precedence. Details also apply the scope predicate to their data query, after the shared permission check. Lists default to limit=20/offset=0, require limit 1–100 and offset 0–9223372036854775807 (2**63-1), sort ascending by created_at/id, and count only the same scoped query. The route and response schema share MAX_OFFSET. Invalid offsets receive a redacted 422 before a database Session or SQL query is started. Count and page statements are not a frozen pagination snapshot under concurrent changes. Reads are point-in-time checks, not transaction-level revocation guarantees.

Errors preserve the existing 401/403/404/422/503/500 policy. Inaccessible and missing spaces have the same 404 body. Workspace input/internal errors use fixed details. All protected responses carry no-store; the shared boundary now also covers method-not-allowed while preserving HTTPException headers such as Allow. CORS adds PATCH/DELETE to GET/POST without changing origins or enabling credentials.

### Workspace writes and the PostgreSQL locking protocol

`backend/app/db/workspace_session.py` exclusively owns each Workspace write Session, begin, commit, rollback and close. It is separate from registration, never uses REGISTRATION_COMMITTED and does not change auth_read_session. PostgreSQL connections explicitly request READ COMMITTED before the first statement and apply SET LOCAL lock_timeout='5s' and statement_timeout='15s'. Connection options reset on return to the pool. SQLite ignores the row-lock clauses and cannot validate this concurrency protocol.

The service locks the actor's User row FOR SHARE, then checks active status. PATCH/DELETE lock the Workspace row FOR UPDATE, then the actor's existing membership row FOR UPDATE, then call require_workspace_permission with that same Session. Scalar authorization queries read persisted values without autoflush/identity-map shortcuts. POST locks/rechecks the account and creates a workspace owned by that verified UUID. Pending business changes must be explicitly flushed before any authorization that is intended to observe them.

The intended serialization points are:

- If disable/remove/downgrade/transfer commits before the relevant lock is acquired, the subsequent READ COMMITTED authorization must observe it and deny as appropriate.
- If this writer acquires its locks first, an account UPDATE/DELETE, Workspace ownership UPDATE/DELETE, or existing membership UPDATE/DELETE needs a conflicting lock and must wait for this writer's transaction to finish. This orders the operations; it is not retroactive cancellation of an already authorized transaction.
- Concurrent Workspace mutations serialize on the Workspace row. Missing memberships do not receive a gap lock: a denied nonmember transaction performs no write. Ownership is read while the Workspace lock is held.

Future member-management, transfer and multi-entity transactions must use a shared order: all required User rows in UUID order, then all Workspace rows in UUID order, then membership rows in user UUID order. Lock actor/target users before Workspace; never lock a member then wait for its Workspace, or upgrade a User lock after acquiring Workspace. Account-management code touching workspaces must follow the same protocol. A writer holding a member lock and then requesting Workspace can deadlock with Workspace deletion/cascade; mixed orders are forbidden. Deadlock, serialization and timeout errors are not automatically retried. The lock/statement timeouts bound individual PostgreSQL waits, not the whole HTTP request duration.

These are implemented PostgreSQL-compatible statements and protocol assumptions, not native execution evidence. Real two-connection tests must separately verify account disable, downgrade/removal, transfer, concurrent deletion, waits, deadlocks and commit acknowledgement loss in an explicitly approved isolated database. No PostgreSQL connection was made for Stage 03C-2 development.

### Workspace commit and cleanup outcomes

Writes flush/refresh data and validate a public DTO before commit. The transaction manager performs an explicit final flush before entering the commit phase. Operation or explicit flush failures occur before commit is called, attempt rollback, and retain the original redacted error classification. This includes database-confirmed DML/constraint rejection before commit. Only a normal return from commit acknowledges success and permits 201/200/204. Post-acknowledgement cleanup faults log only fixed warnings and retain the actual success result. Rollback/close failures never replace a primary rejection or UNKNOWN result. Close falls back to Session.invalidate, then retained Connection.invalidate/close with no unbounded retries. The application cannot force physical release if every underlying driver cleanup operation is broken; this terminal condition needs operational intervention.

Once Session.commit() starts, any ordinary exception is conservatively COMMIT UNKNOWN, reported as a redacted 503 with `Workspace commit outcome is unknown; reconcile before retrying`. This manager does not collect reliable evidence identifying a rejection from the main physical commit. IntegrityError, OperationalError or SQLSTATE 40001/40P01 alone cannot supply that evidence: an after_commit callback may raise the same error after data is durable. A before_commit callback can also add pending changes that fail in commit's implicit flush; that non-applied outcome is still UNKNOWN to this manager. Classification uses the manager's own pre-commit/commit control flow, not SQLAlchemy private attributes, Session.is_active, in_transaction() or connection closure as proof of physical outcome.

The original UNKNOWN exception context is discarded. No automatic retry or claim of successful rollback is made: a server commit may already have happened, and rollback cannot undo it. If commit returned normally, later Session.close failure preserves acknowledged success. Tests distinguish actual database state from what the application can prove by querying a separate Session after each fault. Clients must reconcile the current state before retrying POST/PATCH/DELETE; names are not unique, and no idempotency-key facility is claimed.

### Workspace test and deletion boundaries

The shared auth fixture now explicitly enables SQLite foreign keys. New Workspace fixtures use a unique SQLite shared-memory URI and independent keeper connection, so invalidating the single pooled connection does not erase the database and conceal cleanup/commit outcomes. They retain one pooled connection, enable foreign keys on every checkout-created connection, and assert no checked-out connections at teardown. Real PostgreSQL calls are blocked and synthetic credentials are used.

Tests exercise the production ASGI app, six isolated users, role filtering, pagination, invalid inputs, downgraded/removed/deleted users, error redaction, acknowledged/unknown/rejected commits, rollback and multiple cleanup failures. Offline PostgreSQL compilation checks the actual lock statement order and mocks inspect transaction options; neither proves native row-lock behavior. Existing authentication/RBAC tests are retained.

Deletion relies on the existing WorkspaceMember CASCADE foreign keys and never deletes User records. SQLite tests assert memberships disappear, users and other spaces remain, failure rolls data back, and 204 has no body. Future PDF/experiment tables and external object storage require a separately designed transactional relationship, deletion/retention policy and recoverable external cleanup (for example a reviewed durable cleanup workflow); current Workspace deletion is not proof of future file deletion consistency. No member-writing, ownership-transfer, PDF, RAG, Agent or deployment features are introduced here.

The initial 2026-11-25 development baseline was 566 non-integration cases with one integration case deselected. The first independent review then reproduced commit-classification and offset-overflow failures. On 2026-11-26, 42 new targeted regression cases ran against the original implementation: 25 failed and 17 passed. The implementation fix made all 42 pass; three additional cases cover database rejection combined with rollback/close failures, for 45 added cases in this repair.

Final Python 3.11.6 results are 36 commit-focused cases, 9 offset cases, 89 transaction cases, 78 CRUD cases, 55 isolation cases, 311 existing authentication/RBAC cases, and 611 non-integration cases overall with one integration case deselected. Final commands, pip check and git diff --check exited 0, with no final failures or skips. New after_commit tests exercise POST/PATCH/DELETE with IntegrityError, OperationalError carrying 40001/40P01, and RuntimeError, with and without cleanup faults. They record the callback phase and the same exception propagating from the real Session.commit(), assert one commit/one HTTP response, and independently verify durable data. SQLite-only triggers provide actual pre-commit database rejection; offset tests verify 0, 1, the int64 maximum and invalid inputs without opening a database Session.

Test processes block real .env reads and Psycopg connections. No real PostgreSQL network interruption, row-lock contention, concurrency, migration or Workspace writes were exercised. SQLite fault injection and offline compilation are not native PostgreSQL evidence. These repairs subsequently passed the second independent Stage 03C-2 review and were merged; native concurrency acceptance remains outstanding.

### Stage 03C-3 production membership management

Four production operations are added under `/api/v1/workspaces/{workspace_id}/members`: GET list (200), POST add (201), PATCH `/{user_id}` (200), and DELETE `/{user_id}` (204, empty body). They use TokenSubject and a small WorkspaceRoute subclass, preserving strict JWT/current-account checks, no-store, 405 Allow, redacted errors, and the existing UNKNOWN 503. No separate authentication, role matrix, Engine or commit-classification mechanism is introduced.

POST requires exactly user_id UUID and role; PATCH requires exactly role. Only admin/editor/viewer literals are accepted; unknown fields, malformed UUIDs/roles and empty PATCH return fixed 422 `Invalid member input`. Public MemberResponse is exactly user_id, role, joined_at. Email, hashes and account internals are never serialized. The Owner appears as one virtual owner entry with joined_at=null, derived solely from Workspace.owner_id. Redundant owner membership rows and invalid stored roles are excluded from the ordinary member branch. The API never inserts an owner membership.

| Actor | List | Add | Change existing roles | Remove |
| --- | --- | --- | --- | --- |
| Owner | Yes | admin/editor/viewer | Between all three ordinary roles | All ordinary roles |
| Admin | Yes | editor/viewer | editor/viewer only, checking both old/new values | editor/viewer only |
| Editor/Viewer | Yes | No | No | No |
| Nonmember | 404 | 404 | 404 | 404 |

All member writes reject the real Owner and the actor's own UUID, including same-role requests. An authorized same-role PATCH returns 200 without UPDATE and retains joined_at; repeat DELETE returns member 404. Direct addition targets an already registered, active account. Inactive existing members may still be changed or removed by a qualified manager. No invitation confirmation, user search, account creation, leave or ownership transfer is implemented; direct addition is a separate product choice from invitation acceptance.

List authorization requires member:read. The SQL reuses the existing scoped Workspace query in both count and page, UNION ALLs one virtual owner with valid ordinary members, excludes owner duplicates and sorts owner first, then joined_at/user_id. Pagination defaults to 20/0, limit is 1–100 and offset 0–2**63-1. Total includes the virtual owner and cannot count other workspaces. Count/page remain separate point-in-time reads; concurrent pagination is not a frozen snapshot. The historical OpenAPI response-maximum floating-point representation P3 remains open; actual request and Pydantic bounds are exact.

A read-only preliminary management check prevents unauthorized target-account lookups and locks. Missing/inaccessible workspaces remain indistinguishable 404s; insufficient privileges return 403 before target account status is examined. Authorized managers see fixed member 404 for absent/inactive add targets or missing member targets. Duplicate addition is 409 only if the manager can manage both the existing and requested role; an Admin targeting another Admin still receives 403. No raw database diagnostic or target email is exposed.

The shared `lock_active_accounts` and `lock_workspace_permission` functions were extracted into workspace_access; existing CRUD retains its one-user lock semantics. Member writers acquire all participating User FOR SHARE rows in UUID order, then Workspace FOR UPDATE, then actor/target membership FOR UPDATE rows in user UUID order. Only after these locks are acquired does the service re-read the active actor, authoritative owner and actor role. It reads the target's actual role using a scalar row query, evaluates can_manage_member with both role values, and performs a workspace/user/old-role-scoped mutation in the same transaction. The preliminary context is never the write authority. No target User lock is acquired after Workspace, no role cache/identity-map shortcut is used, and the helpers neither commit nor close their caller's Session.

Absent memberships have no row/gap lock. Cooperative writers serialize on Workspace; the existing composite PK is the final uniqueness guard. Known PK rejection from the explicit pre-commit INSERT flush maps to 409 (PostgreSQL 23505 plus the exact PK name, or SQLite's native PK rejection), while unrelated constraint errors retain their generic failure classification. Commit errors are never caught by this duplicate handler. All writes reuse workspace_write_session: explicit flush before commit, acknowledged success only after normal commit return, every unproven commit exception UNKNOWN, and confirmed success retained after cleanup faults. Clients must reconcile member state after UNKNOWN instead of automatically repeating a write.

Concurrency expectations requiring native PostgreSQL verification:

| Scenario | Expected serialization under the shared protocol |
| --- | --- |
| Two Admins add one target | Workspace orders writers; the later request re-reads membership and gets 409 if authorized for its current role |
| Owner and Admin change one target | Later authorization uses the latest role; promotion to admin by Owner prevents subsequent Admin management |
| Admin is downgraded/removed during a write | If revocation wins Workspace/member locks, final authorization returns 403/404; if the writer wins, its operation finishes before revocation |
| Workspace deletion versus add | Delete first yields Workspace 404; add first may commit and then be removed by the later Workspace cascade |
| Target disable versus add | User SHARE conflicts with account UPDATE; disable first rejects add, add first completes before disable |
| Opposite actor/target UUID order | Every participant requests User rows in the same sorted order; member locks are also sorted, avoiding an application-level reversed order |

Future account/ownership writers must follow the same global order, including all required users before Workspace. No transaction may first lock a member and then wait for Workspace or upgrade a User lock after acquiring Workspace. These rules do not prove absence of all native deadlocks; the existing 5s lock_timeout, 15s statement_timeout, rollback and no-retry policy still apply.

On 2026-11-26, the existing Python 3.11.6 environment passed 144 membership API cases, 45 isolation cases and 67 transaction cases (256 new), and 867 non-integration cases overall, with one integration case deselected, zero failures/skips and exit code 0. pip check and git diff --check passed. The original 611 tests are retained without changes to their test files; conftest only adds a member-route client fixture. Production ASGI requests use six synthetic users and two memory workspaces with FK enforcement. Separate Sessions verify persisted membership changes, unchanged users/workspaces/other memberships, primary-key rejection, after_commit IntegrityError/40001/40P01/runtime failures, cleanup failures, UNKNOWN responses, single commits and single HTTP responses. A stale precheck test exercises native SQLite PK rejection; it does not claim a real two-writer race.

Tests block real .env reads and Psycopg entry points. PostgreSQL lock clauses and member count/page SQL were compiled offline only. No ORM, revision or dependency changes, native PostgreSQL connections, schema changes or business writes were performed. Final concurrency acceptance needs an explicitly authorized isolated database for two-connection downgrade, removal, duplicate add, Workspace deletion, account disable, timeout, deadlock/rollback, READ COMMITTED visibility and commit acknowledgement/network-loss cases. Neither paperpilot_dev nor paperpilot_migration_test is a substitute write-test target.

The existing invalid-Argon2-parameter classification, request-size limits, rate limits, Argon2 resource caps, log/APM redaction, key rotation and HTTPS deployment tasks remain open. Future PDF/document/RAG resources must carry workspace linkage and enforce authorized scope on every query and write. No resource management or Stage 04 functionality is added here. Stage 03C-3 offline development awaits independent review and separately authorized native concurrency acceptance.

## Configuration and security

- The frontend reads `NEXT_PUBLIC_API_BASE_URL`; the `NEXT_PUBLIC_` prefix means the value is intentionally visible in the browser bundle.
- The backend reads `CORS_ORIGINS` as a comma-separated allowlist and defaults to `http://localhost:3000` only.
- Pydantic Settings reads `DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USER`, and `DB_PASSWORD` from `backend/.env` or the process environment.
- `DB_PASSWORD` is represented as a Pydantic `SecretStr`. SQLAlchemy `URL.create` receives the raw value directly so reserved characters are handled without manual URL interpolation.
- `JWT_SECRET_KEY` is represented as a Pydantic `SecretStr`, loaded and validated by `AuthSettings` on first authentication use. Set the 64 hexadecimal characters produced by `secrets.token_hex(32)` in the local `backend/.env` or the process environment, then restart the process when changing the key. The validator checks format and rejects obvious repetition. Entropy comes from secure generation. Health, readiness, and Alembic have independent settings paths.
- Real `.env` files are ignored; `.env.example` contains placeholders only. Logs and API responses must not include credentials or full connection URLs.
- CORS restricts browser origins. Workspace data access must separately enforce authorization for the authenticated user.

## Connection lifecycle

The engine and session factory are created lazily and cached once per process. This keeps `/api/v1/health` operational when database configuration is absent while `/api/v1/ready` accurately reports the database dependency. The engine uses `pool_pre_ping`, a five-connection base pool, up to ten overflow connections, a five-second pool wait, a five-second driver connection timeout, and periodic connection recycling.

Application sessions are dependency-scoped generators: successful use closes the session, exceptions trigger rollback, and the connection is returned to the pool. Stage 02A introduced this provider without persistence. Stage 03A registration explicitly uses function scope and owns its service-level commit/rollback; no startup path creates tables or performs migrations.

## Verification boundary

Readiness unit tests mock the connectivity function to exercise HTTP 200 and 503 contracts. The separately marked `integration` test now uses only the verified isolated test Engine and is skipped by default. It never loads the application .env; CLI/environment opt-in and server identity verification are required. Mock tests are never reported as real database verification.

Integration failures report only the exception type and a generic diagnostic message. Driver/configuration exceptions are caught before calling `pytest.fail(..., pytrace=False)` outside the exception handler, preventing pytest from expanding credential-bearing driver frames or chained exception context. Unit regressions cover both driver and settings failures. Connection failures remain failures, rather than being silently skipped.

Stage 02B model tests use an isolated `sqlite+pysqlite:///:memory:` engine with `PRAGMA foreign_keys=ON`. Test fixtures create tables on their isolated in-memory Engines and dispose them afterward. No application startup code, model import, or PostgreSQL integration test runs schema DDL. Model tests do not load local credentials. PostgreSQL DDL is compiled offline, including expression indexes and named constraints; the real integration test remains the existing read-only `SELECT 1`.

SQLite exercises basic constraints and ORM cascades, but it is not a PostgreSQL compatibility proof: its default `lower()` only handles ASCII case, `VARCHAR(n)` does not enforce PostgreSQL's length limit, UUIDs are stored as hex text, timezone-aware timestamp storage differs, and transaction/concurrency behavior differs. Stage 02C-2B separately exercised the real initial migration and 17 groups of native constraint behavior in the isolated PostgreSQL test database. Expected IntegrityError cases used SAVEPOINTs and all synthetic business data was rolled back. That rehearsal is separate from the repository's 79 pytest cases and is not automatically repeated by pytest. Future revisions require their own reviewed migration and recovery validation.

## ORM schema and metadata

All three models use one `Base(DeclarativeBase)` and `Base.metadata`. Alembic imports `app.models` before supplying `Base.metadata` as `target_metadata`; importing `Base` alone does not discover models. Model imports only register Python mappings and never create an engine or load database settings.

The metadata naming convention generates `pk_<table>`, `fk_<table>_<column>_<target>`, `ck_<table>_<check-name>`, `uq_<table>_<column>`, and `ix_<table>_<column>`. The email expression index has the explicit stable name `uq_users_email_lower`. This makes future migration output reproducible.

| Model / table | Columns (all NOT NULL) | Keys, checks, and secondary indexes |
| --- | --- | --- |
| User / `users` | `id Uuid`, `email String(320)`, `password_hash String(255)`, `is_active Boolean`, `created_at` / `updated_at DateTime(timezone=True)` | UUID PK; unique index on `lower(email)`; nonblank email/hash checks; active defaults to true |
| Workspace / `workspaces` | `id Uuid`, `owner_id Uuid`, `name String(150)`, `created_at` / `updated_at DateTime(timezone=True)` | UUID PK; owner FK to users with RESTRICT; owner index; nonblank name check; names are not unique |
| WorkspaceMember / `workspace_members` | `workspace_id Uuid`, `user_id Uuid`, `role String(16)`, `joined_at DateTime(timezone=True)` | Composite PK `(workspace_id, user_id)`; workspace/user FKs with CASCADE; user index; role CHECK allows admin/editor/viewer only; default viewer |

`Uuid(as_uuid=True)` maps Python `uuid.UUID` to native PostgreSQL UUID. A shared UUID mixin uses the callable `uuid4`, generating a fresh ID at SQLAlchemy insert time, not when constructing an unsaved object. There is no UUID server default, so direct SQL inserts must supply IDs. WorkspaceMember uses its two FKs as the primary key and has no redundant ID or workspace-only index; the composite primary key already supports workspace-prefix lookups.

The unique email index provides database protection against case variants according to PostgreSQL `lower()` and the database's locale/collation. It does not implement full Unicode case folding, Unicode normalization, whitespace stripping, or email syntax validation. Unicode case behavior may differ across collations and from Python `casefold()`; no universal Unicode-equivalence claim is made. Registration and login enforce the same ASCII-only strip/validate/lower policy. Blank checks for email, password hash, and workspace name trim an explicit shared Unicode White_Space set; they reject empty/whitespace-only strings without modifying stored text. Invisible formatting characters outside that set, such as zero-width space, are not treated as whitespace. Registration writes service-produced Argon2id hashes for login. Mailbox verification is future work. The default model representation omits column values.

## Ownership, relationships, and deletion

`Workspace.owner_id` is the unique authoritative ownership source. An owner does not need a membership row, and deleting or losing a membership cannot change ownership. The resolver grants `owner` only from `Workspace.owner_id`. WorkspaceMember records ordinary collaborators with admin/editor/viewer roles and defaults to viewer.

Relationships use explicit `back_populates`: User.owned_workspaces ↔ Workspace.owner, User.workspace_memberships ↔ WorkspaceMember.user, and Workspace.memberships ↔ WorkspaceMember.workspace. There are no overlapping direct many-to-many shortcuts. A user may own many workspaces, including several with the same name.

Deleting a user who still owns a workspace is rejected by the owner FK's `ON DELETE RESTRICT`. User.owned_workspaces uses `passive_deletes="all"` with no delete cascade so ORM deletion does not null out owner IDs or delete owned workspaces, even when that relationship is loaded. Workspace and ordinary user deletion remove associated membership rows using `ON DELETE CASCADE`; membership collections also use ORM delete-orphan cascade and passive deletion for unloaded collections.

Database constraints guarantee references, non-null/nonblank values, email uniqueness under the stated case rules, valid member roles, unique member pairs, and the deletion policies. Request authorization also checks the active account, workspace linkage and required permission. Stage 03C-3 member writers prevent redundant owner membership. Future resource endpoints must scope each query or mutation to the authorized workspace. Database row-level security remains outside this stage.

## Timestamp behavior

User and Workspace share non-null `created_at` and `updated_at`; WorkspaceMember has non-null `joined_at`. All use `DateTime(timezone=True)` and database `now()` insert defaults. `updated_at` also has SQLAlchemy `onupdate=func.now()`, applied when an ORM (or SQLAlchemy Core) UPDATE omits an explicit updated_at value. No-op ORM flushes do not issue an UPDATE. Direct SQL bypasses this client-side rule and leaves updated_at unchanged unless the statement sets it. There is no update trigger or automatic database-side update rule; such a requirement would need a later migration. PostgreSQL `now()` is the transaction timestamp, so several updates in one transaction need not have strictly increasing timestamps.

## Intended growth path

Future capabilities should be added behind tested service boundaries. Stage 02C applied the initial schema to the validated local development environment. Stage 03A implements registration, input normalization, and password hashing. Stage 03B verifies identity at `/me`. Stage 03C-1 resolves current workspace roles and permissions, Stage 03C-2 applies them to Workspace CRUD, and Stage 03C-3 extends them to membership management. Ownership transfer, invitations/acceptance, mailbox verification and PDF/resource tenant isolation remain separate milestones.

## Alembic configuration and version history

Alembic 1.20.1 is pinned in `backend/requirements.txt`. `backend/alembic.ini` resolves its script directory and Python import path relative to the INI file and contains no database URL. `backend/alembic/env.py` imports `app.models` and supplies the shared `Base.metadata` as `target_metadata` with type comparison enabled. No application startup path imports this environment or invokes migrations.

Offline mode configures the PostgreSQL dialect directly with literal binds. It neither loads Settings nor constructs or connects an engine. From `backend`, run `.\.venv\Scripts\python.exe -m alembic upgrade 20261120_0001 --sql` to preview the initial SQL from base. Keep `--sql` for this preview; an online migration is a separate approved operation. The output includes transactional DDL and version tracking statements without applying them or inspecting a live database. An already migrated database must not receive this initial SQL again. `heads` and `history --verbose` inspect script history without a connection; `current` reads the version of the configured database and requires correct target configuration.

Online mode uses an explicitly supplied `Config.attributes["connection"]` when present; otherwise it reuses `app.db.session.get_engine()` and the existing Settings/credential lifecycle. It does not interpolate passwords into INI strings, configure URL logging, or create a second credential source. Driver and Settings failures become generic Alembic CommandError messages containing only the exception type, with sensitive messages and chained context suppressed. External tooling must also avoid logging connections, locals, or full tracebacks containing credentials.

The sole initial revision is `20261120_0001`, in `backend/alembic/versions/20261120_0001_create_initial_tables.py`, with no parent. It creates users, then workspaces, then workspace_members; the stable PK/FK/CHECK/index names match the ORM. It freezes column types, server defaults, nonblank whitespace checks, the unique lower(email) index, RESTRICT ownership deletion, CASCADE membership deletion, the member pair PK, and allowed roles. It intentionally adds no UUID server default or updated_at trigger. Revision code imports only SQLAlchemy and Alembic, preserving historical schema independently of future models. Future schema changes require additional revisions rather than editing an already applied revision.

The script head describes available migration history, not the version of every database. The validated local development database has one alembic_version row, 20261120_0001. Every new database or environment has independent migration state: cloning the repository, installing dependencies, or starting FastAPI does not create or upgrade its schema. New environments must verify their identity, existing objects, privileges and version state, review SQL, prepare backups, and obtain explicit approval before applying a pinned revision through a controlled connection. Never stamp existing tables merely to bypass a conflict. Initial downgrade removes all three business tables and data and is not a lossless recovery strategy.

`backend/tests/test_migrations.py` captures real Alembic operations in an offline MigrationContext and compares columns, types, nullability, server defaults, named constraints, FK policies and indexes against the ORM. It checks one base/head, creation order, SQL generation, and reverse downgrade ordering. Connection guards reject real engine/driver connections. Offline tests forbid Settings loading and engine construction; online environment and error tests use mocks only. PostgreSQL DDL is not executed by these tests. Native PostgreSQL upgrade and constraint validation was performed separately in Stage 02C-2B; no real downgrade rehearsal is claimed.

## Controlled migration execution and failure handling

The approved Stage 02C migration used a dedicated Engine built from existing Settings with `URL.set(database=the_verified_target)`; it did not reuse a cached application Engine. The actual Connection was checked for database, user, owner, schema, server address, port, and PostgreSQL version. The outer transaction applied `SET LOCAL search_path TO public` and bounded lock/statement timeouts. Its Connection was supplied through `Config.attributes["connection"]`. Runtime guards verified that env.py configured that exact Connection and blocked the default `get_engine()` fallback and new connections during the upgrade. The migration target was explicitly `20261120_0001` rather than an unreviewed future head.

An outer PostgreSQL transaction enclosed the supported DDL and version insert. Version uniqueness, table/column definitions, defaults, 3 business PKs, 3 FKs, 4 CHECKs and 3 secondary indexes were verified on the same Connection before commit. A new read-only Connection then confirmed persistence, zero business rows, no unexpected objects, and no detectable ORM metadata drift. No synthetic business records were written to the development database.

Failures before commit require transaction rollback, connection closure, and a fresh read-only inspection of actual objects and version state. A lost connection during commit must first be classified COMMIT UNKNOWN; neither successful commit nor successful rollback may be assumed. Inspect persistence after reconnecting and stop if it cannot be determined. Do not automatically retry, stamp, downgrade, delete tables, or rebuild a database to hide failure. The completed local migration received a commit acknowledgement and passed independent persistence checks.

## Backup, restoration, and environment boundaries

Stage 02C produced non-overwriting PostgreSQL custom archives before and after the local migration. Backups and restricted verification records remain outside Git; no credentials, private absolute paths, or archive contents belong in repository documentation. Each archive is identified by timestamp and SHA-256, its source database is checked, and pg_restore listing and SQL rendering validate readability without executing the rendered SQL. Credentials must not appear in command-line arguments, output, or persistent verification logs.

The pre-migration empty development archive was actually restored into an independently created test database with the intended owner. Restoration used an explicit target, `--exit-on-error` and `--single-transaction`, without `--create` or `--clean`; identities and empty-object baselines were checked before migration. This proves only that empty-database recovery workflow. The post-migration archive has been read and rendered but has not been restored in a separate rehearsal. Archive readability is not recovery proof, and future real business data requires representative recovery checks of objects, permissions and data. Applying a migration to one local database does not deploy a cloud environment or migrate any other database.

## Local completion evidence

The Stage 02C-2B/2C-2 execution records report successful isolated rehearsal, 17 native constraint test groups, committed local development migration, 79 passing pytest cases, a clean pip check, and verified post-migration archive readability. These are historical execution results.

During the documentation review on 2026-11-20, fresh read-only checks confirmed PostgreSQL 17.12, public schema, unique revision 20261120_0001, all four expected tables, 3 business PKs/3 FKs/4 CHECKs/3 secondary indexes, zero rows in each business table, and no ORM differences. This review did not apply migrations or repeat write-based constraint tests. After the documentation edits, the project's Python 3.11.6 virtual environment ran the full pytest suite from the backend directory: 79 passed, 0 failed, 0 skipped, exit code 0. A fresh pip check also passed with exit code 0. Read-only structure checks before and after the regression were identical.

### Stage 03A evidence

The Stage 02C records above are preserved historical evidence. On 2026-11-20 the project's Python 3.11.6 environment executed 20 security tests, 54 registration API tests, 152 non-integration tests (1 deselected), and the full 153-test suite including the existing PostgreSQL SELECT 1. Every command exited 0; the full suite had no failures or skips. pip check also passed. The 74 added tests do not replace or remove any of the original 79 tests.

PostgreSQL connections in the test processes were restricted to read-only; registration API tests additionally block PostgreSQL connection attempts. Fresh read-only comparisons before and after validation confirmed unchanged schema/version fingerprints and zero business rows in paperpilot_dev and paperpilot_migration_test. No PostgreSQL registration writes, schema changes, real concurrency test, or new authentication database are executed by this stage.


## Stage 03C-4A audit and Stage 03C-4B-0 test safety

This stage implements test infrastructure and offline verification only. No dedicated PostgreSQL instance, database, roles or environment marker exists as a result of this work. The candidate directory D:\PostgreSQL\paperpilot_concurrency_17 and loopback port 55432 remain proposals. No real PostgreSQL connection, schema operation, native concurrency test or fault injection was performed.

### Review findings and trust boundary

The first independent review was FAIL despite 1017 passing offline tests. F1 trusted a caller-created PgOptIn; F2 missed cached Engines/Sessions/borrowed Connections and the old direct integration entry; F3 missed native exports and retained aliases; F4 installed guards after early pytest hooks; F5 accessed .info before checking invalidation; F6 could preserve an old ticket when preflight and cleanup failed; F7 only stopped new connections; F8 pre-installed the subject guards in startup tests.

The repair distinguishes intent, offline simulation and live runtime permission. These Python mechanisms protect reviewed repository code from accidental or unsupported database access. They are not a sandbox against hostile Python, tampered dependencies, direct ctypes/libpq calls, or previously captured C function references. Private names and immutable values are not security boundaries.

### Trusted process entry and runtime permission

backend/run_pg_tests.py calls the stdlib-only launcher. It validates approved CLI options and repository test paths, removes DB_*, JWT_SECRET_KEY, PYTEST_* and Python-path/startup injection settings, rejects inherited PG* when requesting PG mode, and starts a fresh python -I -B interpreter. Offline mode also removes test credentials. The new process inherits no usable parent Engine, pool, Session or borrowed Connection.

bootstrap installs the .env audit, supported driver/SQLAlchemy guards and database-library log redaction before importing pytest or approved plugins. Plugin auto-loading is disabled. Only the safety plugin and the statically listed _startup_probe audit plugin are accepted; arbitrary -p/-c/-o and paths outside backend/tests are refused. The virtual environment and repository are trusted inputs, not hostile-code containment.

Ordinary pytest remains offline by default. Its backend/conftest.py installs the plugin before application fixtures and skips legacy integration plus postgres_integration, postgres_concurrency and postgres_fault_injection. It cannot outrun arbitrary manually injected early plugins; such execution is outside the ordinary offline trust boundary. Ordinary pytest rejects live/fault options. The recommended offline command is python -B run_pg_tests.py -q; the same controlled entry is mandatory for any future live test.

PgOptIn and preflight validate intent and target only; neither issues a driver permit. runtime binds actual pytest Config, the current marked item, setup/call/teardown phase, process ID and a per-item nonce. It rechecks actual --pg-test and PAPERPILOT_PG_TEST_ENABLE=1; fault access also requires both actual --pg-fault and PAPERPILOT_PG_TEST_FAULT_ENABLE=1. Collection/early hooks and session completion cannot connect. Engines belong to the creating item's runtime and cannot be reused by another test. Technical opt-in does not grant permission to operate any database in this stage.

### Connection factories, caches and native entry points

The live factory requires runtime permission and registers its dedicated bounded Engine. Offline simulations use a separate built-in synthetic DBAPI factory; no injectable native connector and no live-driver permission is available through that factory. Synthetic SQL execution checks a checkout-bound guard whose underlying transport has the exact built-in DBAPI type. Legacy integration and application injection require registered live provenance; reporting unit tests exercise a separate pure SELECT-one formatter with doubles.

At startup, a best-effort heap check rejects pre-existing unregistered PostgreSQL Engines. This supplements fresh-process isolation; it is not a universal object enumeration guarantee. Engine.connect issues a bounded internal acquisition scope; public raw_connection and direct Pool borrowing are refused. SQLAlchemy execution/commit gates and guarded DBAPI handles recheck the current checkout, runtime, lease and fuse. Session/Transaction commits follow the same execution policy; unsupported two-phase transactions are refused separately. Rollback/close cleanup remains possible after revocation. Cleanup and dispose are not treated as proof that borrowed physical connections were closed.

The current Psycopg 3 exports, including psycopg_binary.pq.PGconn and the real generator chain, are guarded. Direct sync, async and PQ access is denied; only the synchronous controlled creator can reach the native driver with exact parameters. Saved Python connect aliases are tested through the real Python implementation rather than a substituted high-level connect method. Captured native/C references cannot reliably be revoked in-process: a formal boundary test deliberately reaches an independent native substitute once. A fresh reviewed process, no application credentials, restricted plugins, and future OS/network target isolation are required; no stronger guarantee is claimed.

### First-connect initialization and server attestation

Acceptance of the future isolated PostgreSQL 17 test instance must verify that the effective `max_prepared_transactions` is `0`, which disables prepared transactions at the server. This remains an unverified acceptance requirement. Client-side guards independently reject two-phase transaction controls.

The live Engine uses READ COMMITTED, four bounded pool slots, connection timeout 5s, lock_timeout 5s and statement_timeout 15s. pool_pre_ping and unnecessary native hstore adaptation are disabled so they cannot add unverified checkout SQL. Full bounded attestation runs on every SQLAlchemy lease.

SQLAlchemy's first-connect dialect initialization precedes engine_connect. A narrowly scoped callback binds Engine, Connection ID and DBAPI ID, checks closed/invalidated before accessing DBAPI properties, and permits only the pinned PostgreSQL dialect's three fixed exec_driver_sql strings: version, current_schema and standard_conforming_strings. Raw dialect isolation-level inspection is a trusted library read. Arbitrary SQL and business commits gain no temporary permission; the context resets immediately. Initialization or driver-creation failure permanently trips that Engine. Formal tests use real create_engine and PGDialect initialization with a synthetic DBAPI; this is not evidence about a real server.

Preflight keeps the exact whitelist: 127.0.0.1 / 55432 / paperpilot_concurrency_test / paperpilot_test_app / canonical UUID4. PgTestSettings has no application defaults, dotenv or file-secret source; password is SecretStr and URL.create constructs the URL. PG* implicit libpq settings are rejected. Test AuthSettings(_env_file=None) uses a synthetic signing key instead of the application JWT key.

Full attestation checks current database, session/current users, server address/port/version 17, explicit/effective search path, restricted role attributes and memberships, database/schema/TEMP/TRUNCATE privileges, object owners, environment marker, sole revision and reflected schema. The marker contract remains test_control.environment(id=1, environment_id UUID, purpose='paperpilot-postgres-tests'), owned by paperpilot_test_owner and immutable to the application test role. Client UUID configuration alone cannot prove the target.

Columns/types/nullability/defaults, named PK/FK/CHECK definitions and indexes must match reviewed metadata. Catalog checks reject invalid/deferred constraints and invalid indexes; partial lower(email) unique indexes do not satisfy the global unique-index contract. SQL normalization preserves string literals. Only revision 20261120_0001 is approved. No identity/schema/privilege check was weakened to repair an entry path.

### Lease, failure and resource lifecycle

A weak registry is the authoritative logical lease source; .info is only a mirror. Closed/invalidated checks precede any property that could reconnect. Reverification first revokes the old registry lease, then performs preflight and read-only bounded attestation. Rollback must end the verification transaction before stamping a fresh Connection/driver-bound lease. All-cleanup-failed paths cannot restore the removed lease.

Fuse/dispose revoke every registered lease before physical cleanup. Already borrowed Connections, Sessions and Transactions must pass the execution/commit gate; a previously good lease cannot override a failed/closed Engine or expired runtime. Checkin removes the mirrored ticket, while close/invalidate/detach revoke logical leases. A new checkout gets a new lease even when its DBAPI connection is reused.

Live-marked test failures stop subsequent tests; fixture teardown disposes the Engine and reports outstanding checkouts. Future concurrency/fault workers must stop, join and return all leases before test completion; these guards do not implement worker orchestration or OS network isolation. Future real concurrent workloads must separately validate resource cleanup and lease-registry behavior under their threading model.

### Alembic and diagnostic boundaries

prepare_test_migration only prepares a Config bound to an attested Connection, current lease and pinned revision. Test-mode env.py refuses missing/replaced/stale bindings before application Engine fallback. It never invokes an Alembic command. Ordinary non-test Alembic behavior is retained and tested with doubles. Empty-database provisioning needs a separately authorized Owner/Migrator workflow and is not replaced with create_all.

Final exception replacement alone cannot sanitize earlier library logs. The dedicated test process installs a LogRecordFactory wrapper before handlers/queues receive psycopg/sqlalchemy records; it replaces message/arguments and clears exception/stack details. This intentionally suppresses database-library diagnostics at all levels, including DEBUG and Pool cleanup. Application loggers retain their normal behavior. Tests inject synthetic secrets into real Psycopg error handling and real SQLAlchemy Pool cleanup. This does not promise protection from malicious handlers, code dumping locals, or hostile logging-factory replacement.

### Offline repair evidence (2026-11-27)

The final targeted group passed 60 cases; the safety directory passed 210; all non-integration tests passed 1077 with one deselected; default pytest passed 1077 with the legacy integration safely skipped. All four pytest processes exited 0. pip check reported no broken requirements and git diff --check passed. The original 1017 cases remain and 60 new cases were added.

The first 12 formal reproductions were red before implementation and green afterwards. Later tests also reproduced and fixed first-connect PostgreSQL dialect initialization, library-internal secret logging and invalidated initialization callback handling. The old 55 temporary probes were not represented as an unchanged rerun.

Each of the four final pytest runs used an independent native fence before the subject startup. The parent native driver count was zero; aggregate child substitute calls were four: three intentional controlled-runtime/diagnostic cases and one explicit saved-native-reference boundary case. No real native connection occurred. One environment audit event in the safety/full suites was deliberately emitted by the original sys.audit unit test without opening the file; child environment-audit counts were zero. Skipped tests, synthetic PGDialect initialization and driver substitutes are not native database evidence.

Original safety-test names, parametrizations and assertions were compared against the pre-repair sources. Existing 150 safety cases and 867 other cases remain, with positive simulations redirected to the explicit non-native factory. Startup subprocesses never pre-install subject guards; only independent native substitutes precede the real startup chain. These results permit a second independent review, not native PostgreSQL testing or automatic publication.

### G1–G3 repair after the second independent review

The second review found raw DBAPI access, two-phase commits and nested rollback gaps despite passing suites. The current baseline is 1089 non-integration cases: 1077 plus the separate twelve registration-cleanup regressions. Those business changes are outside this repair.

`dbapi.py` wraps the Pool creator before the first dialect callback, then rotates the public guard at each checkout while retaining the physical transport. Engine.connect is the only supported acquisition entry; the scope is registered, Engine-bound and expired in finally. Direct raw/pool acquisition cannot create a new transport or obtain an unverified handle. Checkin expires the old handle, including retained cursors; an old handle cannot roll back the next borrower's transaction. Normal SQLAlchemy Connections still work and receive a full attestation at each checkout. A prior identity-reuse assertion now checks unchanged physical identity plus a distinct public handle and rejection of the old handle.

Guarded cursor execute/executemany, result fetching and commit require the current attested SQLAlchemy owner and full runtime/Engine/lease checks. Cursor.connection returns the guard, never a native connection. Unsupported native extensions and pgconn access are refused. Rollback on the current checkout does not require a live business lease; obsolete handles cannot use this cleanup permission. Native pointer capture or deliberate access to private implementation objects remains outside Python's trust boundary.

The cursor's connection binding is read-only. Custom native row/cursor factories are not supported, so callbacks cannot receive and retain an unguarded native cursor through this interface. Connection configuration is limited to the reviewed transaction flags and requires a completed business lease once an owner has been bound; an in-progress attestation cannot enable autocommit by borrowing the acquisition scope.

During first-connect initialization, the existing three exact SQLAlchemy queries and the fixed raw isolation-level read remain narrowly scoped. During attestation, a registered statement scope binds the actual SQLAlchemy Connection, checkout and actual ExecutionContext. Only fixed attestation controls/identity queries or a compiled SELECT with an unchanged wire statement may reach the DBAPI; the server-side READ ONLY transaction remains mandatory. Raw SQL/commit cannot inherit the general `_verifying` flag. Scopes reset on exceptions; a pending approved statement is single-use. This is not a new general SQL text-prefix bypass.

Two-phase transactions are unsupported. Global SQLAlchemy events, TwoPhaseTransaction.commit, per-Engine dialect methods and DBAPI tpc_begin/tpc_prepare/tpc_commit refuse them before any native begin/prepare/commit. A known local legacy transaction may only roll back under a capability tied to its actual transaction, XID, Connection and checkout; recover/foreign XIDs are refused. Failed legacy rollback invalidates the connection and reports a redacted error. No new prepared transaction is created by this infrastructure.

`cleanup.py` derives one-shot SAVEPOINT rollback permission from the actual current active NestedTransaction, current active root transaction and previously attested checkout. It is issued into a weak registry and cannot be obtained by merely constructing a context object. The policy accepts only that exact RollbackToSavepointClause and its actual compiled execution context/wire SQL, with no parameters or executemany. A single matching DBAPI operation consumes it. It permits no new SELECT, DML, DDL, COMMIT, SAVEPOINT or RELEASE SAVEPOINT after a fuse. Direct text, wrong names, forged capabilities, other Engines and copied expired contexts are refused. Both rollback and nested close use the bounded path; failures revoke permission before invalidation and preserve generic diagnostics.

Formal G1/G2/G3 regressions use real SQLAlchemy APIs, actual PGDialect_psycopg first-connect/transaction paths and synthetic DBAPI counters. Legacy 2PC objects are constructed with a test-only no-op begin implementation restored before assertions; no real or simulated PREPARE is needed. These tests are self-contained in the repository. Independent external native substitutes establish test safety before subject startup; native PostgreSQL, online migrations and instance provisioning remain unapproved.

The third review follow-up restricts DBAPI metadata to reviewed fields. ConnectionInfo is a read-only transaction status snapshot. Reading it requires the current checkout and a verified lease, or the existing first-connect scope before a SQLAlchemy owner is bound. The final guarded cursor path checks plain SQL text after SQLAlchemy compilation and accounts for Psycopg's server-side bind placeholder conversion. It rejects textual two-phase controls, multiple commands and input that it cannot classify safely, such as ordinary string literals with backslashes whose meaning depends on the session setting. Existing exact initialization and SAVEPOINT rollback scopes remain in place.

The final 2026-11-28 offline validation passed 52 targeted cases, 262 security cases, 1141 non-integration cases with one deselected, and 1141 default cases with the single live integration skipped. All exited 0; pip check and git diff --check passed. The first 37 regressions were red before repair (23 failed, 14 passed); fifteen more boundary cases were then added. Collection comparison retains all prior 1090 nodes, including the integration node, and adds 52; none were removed. Comparison normalizes only the existing protected_405 parameter's collection-time uuid4 value, leaving all other identifiers unchanged; raw collection logs are retained.

The targeted process recorded zero independent native substitute calls and zero built-in physical substitute calls. Each final security/non-integration/default run recorded zero independent native substitute calls and four expected built-in physical substitute calls from existing positive/boundary tests. Their one environment audit event was the explicit sys.audit test, not a file read. Actual PostgreSQL connections, actual database SQL and online migrations remained zero. The 49 application/frontend/migration files matched the preceding review fingerprints. These are developer repair results requiring another independent review, not approval for publication or native database operations.
