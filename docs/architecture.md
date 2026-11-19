# PaperPilot ResearchOps Architecture

## Stage 02A scope

Stage 02A adds synchronous PostgreSQL connectivity to the existing web UI and API boundary. It introduces configuration, an SQLAlchemy 2.x engine, Psycopg 3, session lifecycle management, and a readiness probe. It does not introduce business tables, migrations, queues, containers, caches, agent frameworks, or model providers.

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

## Intended growth path

Future capabilities should be added behind tested service boundaries rather than placed directly in the route module. Business models and migrations should be introduced in a later stage once their ownership and lifecycle are defined.

