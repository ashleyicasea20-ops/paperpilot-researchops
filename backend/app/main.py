"""FastAPI application entry point for PaperPilot ResearchOps."""

from __future__ import annotations

import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

DEFAULT_CORS_ORIGINS = "http://localhost:3000"


class HealthResponse(BaseModel):
    """Public health-check response."""

    status: str
    service: str
    version: str


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
    allow_methods=["GET"],
    allow_headers=["*"],
)


@app.get("/api/v1/health", response_model=HealthResponse, tags=["system"])
async def health_check() -> HealthResponse:
    """Report that the API process is ready to receive requests."""

    return HealthResponse(
        status="ok",
        service="paperpilot-api",
        version="0.1.0",
    )

