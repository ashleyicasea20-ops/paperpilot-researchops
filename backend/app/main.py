"""FastAPI application entry point for PaperPilot ResearchOps."""

from __future__ import annotations

import os

from fastapi import FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from app.api.v1.auth import router as auth_router
from app.api.v1.login import router as login_router
from app.api.v1.workspaces import router as workspace_router
from app.db.session import DatabaseUnavailableError, check_database_connection

DEFAULT_CORS_ORIGINS = "http://localhost:3000"


class HealthResponse(BaseModel):
    """Public health-check response."""

    status: str
    service: str
    version: str


class ReadinessResponse(BaseModel):
    """Public database-readiness response."""

    status: str
    database: str


def get_cors_origins() -> list[str]:
    """Read a comma-separated local development origin allowlist."""

    configured_origins = os.getenv("CORS_ORIGINS", DEFAULT_CORS_ORIGINS)
    return [origin.strip() for origin in configured_origins.split(",") if origin.strip()]


app = FastAPI(
    title="PaperPilot ResearchOps API",
    description="Backend API for the PaperPilot AI research workflow platform.",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=get_cors_origins(),
    allow_credentials=False,
    allow_methods=["GET", "POST", "PATCH", "DELETE"],
    allow_headers=["*"],
)

app.include_router(auth_router)
app.include_router(login_router)
app.include_router(workspace_router)


@app.get("/api/v1/health", response_model=HealthResponse, tags=["system"])
async def health_check() -> HealthResponse:
    """Report that the API process is running, independent of PostgreSQL."""

    return HealthResponse(
        status="ok",
        service="paperpilot-api",
        version="0.1.0",
    )


@app.get(
    "/api/v1/ready",
    response_model=ReadinessResponse,
    tags=["system"],
    responses={503: {"description": "Database is unavailable"}},
)
def readiness_check() -> ReadinessResponse:
    """Report whether the API can execute a query against PostgreSQL."""

    try:
        check_database_connection()
    except DatabaseUnavailableError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database is unavailable",
        ) from exc

    return ReadinessResponse(status="ok", database="ready")

