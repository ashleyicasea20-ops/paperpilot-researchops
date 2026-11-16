# PaperPilot ResearchOps Architecture

## Phase 1 scope

Phase 1 establishes a deliberately small, runnable boundary between the web UI and API. It does not introduce databases, queues, containers, agent frameworks, or model providers. Those choices should follow concrete product requirements.

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
```

The homepage is mostly a React Server Component. Only the health-status widget is a Client Component because it needs browser lifecycle state and a cross-origin HTTP request.

## Repository boundaries

- `frontend/` owns the browser application, presentation, and public runtime configuration.
- `backend/app/` owns HTTP APIs and application behavior.
- `backend/tests/` owns API contract and middleware checks.
- `docs/` records architecture decisions that apply across both runtimes.

The Python virtual environment and Node.js dependencies remain local build artifacts and are excluded from Git.

## API contract

`GET /api/v1/health` returns a stable readiness payload:

```json
{
  "status": "ok",
  "service": "paperpilot-api",
  "version": "0.1.0"
}
```

The `/api/v1` prefix creates room for future non-breaking additions and an explicit migration path for breaking API changes.

## Configuration and security

- The frontend reads `NEXT_PUBLIC_API_BASE_URL`; the `NEXT_PUBLIC_` prefix means the value is intentionally visible in the browser bundle.
- The backend reads `CORS_ORIGINS` as a comma-separated allowlist and defaults to `http://localhost:3000` only.
- Secrets are not required in Phase 1. Real `.env` files are ignored; `.env.example` files contain safe templates only.
- CORS is a browser access control, not authentication. Authentication and authorization must be designed before protected research data is introduced.

## Intended growth path

Future capabilities should be added behind tested service boundaries rather than placed directly in the route module. Likely later areas include document ingestion, retrieval with page-level citations, experiment metadata, and long-running research jobs. Their storage and orchestration technologies are intentionally undecided in Phase 1.

