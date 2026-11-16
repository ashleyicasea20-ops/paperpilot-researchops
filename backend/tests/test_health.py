"""Health endpoint and local-development CORS tests."""

import asyncio

import httpx

from app.main import app


async def request(
    method: str,
    path: str,
    *,
    headers: dict[str, str] | None = None,
) -> httpx.Response:
    """Send an in-process HTTP request through the complete ASGI stack."""

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://testserver",
    ) as client:
        return await client.request(method, path, headers=headers)


def test_health_check_returns_expected_payload() -> None:
    response = asyncio.run(request("GET", "/api/v1/health"))

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "service": "paperpilot-api",
        "version": "0.1.0",
    }


def test_local_frontend_cors_preflight_succeeds() -> None:
    response = asyncio.run(
        request(
            "OPTIONS",
            "/api/v1/health",
            headers={
                "Origin": "http://localhost:3000",
                "Access-Control-Request-Method": "GET",
            },
        )
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:3000"
    assert "GET" in response.headers["access-control-allow-methods"]

