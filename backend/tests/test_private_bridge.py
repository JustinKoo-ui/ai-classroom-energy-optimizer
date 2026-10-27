"""ASGI boundary tests for the optional authenticated monitoring bridge."""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI, HTTPException, Request

from app.api.v1.routers.analytics import router as analytics_router
from app.api.v1.routers.device_control import _require_loopback as require_hardware_loopback
from app.api.v1.routers.device_control import router as hardware_router
from app.api.v1.routers.environment_analysis import router as environment_router
from app.core.config import Settings
from app.core.private_bridge import (
    PROXY_TOKEN_HEADER,
    PrivateBridgeMiddleware,
    require_monitoring_access,
)
from app.domain.local_hardware import LIGHT_DEVICE_ID, LOCAL_CLASSROOM_ID
from app.repositories.environment import LatestEnvironmentReading
from app.repositories.occupancy import CurrentOccupancy
from app.schemas.analyst import AnalystResponse

TOKEN = "test-monitoring-bridge-token-with-32-plus-characters"
REPORT_TOKEN = "separate-report-access-token"
BASE = f"/api/v1/classrooms/{LOCAL_CLASSROOM_ID}"


def settings(**overrides):
    return Settings(
        _env_file=None,
        **{
            "database_url": "postgresql+asyncpg://unused",
            "classroom_api_proxy_enabled": True,
            "classroom_api_proxy_token": TOKEN,
            "analyst_access_token": REPORT_TOKEN,
            **overrides,
        },
    )


def app(**overrides):
    instance = FastAPI()
    instance.add_middleware(PrivateBridgeMiddleware)
    instance.include_router(environment_router)
    instance.include_router(analytics_router)
    instance.include_router(hardware_router)
    instance.state.analytics_settings = settings(**overrides)
    instance.state.local_hardware_control_enabled = True
    instance.state.sessions = MagicMock()
    instance.state.environmental_analyst = SimpleNamespace(
        generate=AsyncMock(
            return_value=AnalystResponse(status="unavailable", message="No provider")
        )
    )
    return instance


async def send(instance, path, *, method="GET", host="203.0.113.10", headers=None, body=None):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=instance, client=(host, 1234)),
        base_url="http://test",
    ) as client:
        return await client.request(method, path, headers=headers, json=body)


@pytest.mark.parametrize("host", ["127.0.0.1", "203.0.113.10"])
@pytest.mark.parametrize("provided", ["", "wrong-token", "other-token" * 10])
async def test_invalid_explicit_bridge_credentials_never_fall_back_to_local_access(host, provided):
    instance = app()
    response = await send(instance, f"{BASE}/analytics", host=host,
                          headers={PROXY_TOKEN_HEADER: provided})
    assert response.status_code == 403
    instance.state.sessions.assert_not_called()
    assert TOKEN not in response.text


@pytest.mark.parametrize("overrides", [
    {"classroom_api_proxy_enabled": False},
    {"classroom_api_proxy_token": None},
    {"classroom_api_proxy_token": "short"},
    {"classroom_api_proxy_token": " " * 40},
])
async def test_bridge_is_fail_closed_when_disabled_or_missing_a_usable_secret(overrides):
    instance = app(**overrides)
    response = await send(instance, f"{BASE}/analytics", headers={PROXY_TOKEN_HEADER: TOKEN})
    assert response.status_code == 403
    instance.state.sessions.assert_not_called()


async def test_duplicate_bridge_headers_are_rejected():
    instance = app()
    response = await send(instance, f"{BASE}/analytics",
                          headers=[(PROXY_TOKEN_HEADER, TOKEN), (PROXY_TOKEN_HEADER, TOKEN)])
    assert response.status_code == 403
    instance.state.sessions.assert_not_called()


async def test_remote_request_without_bridge_auth_remains_denied():
    instance = app()
    response = await send(instance, f"{BASE}/analytics")
    assert response.status_code == 403
    instance.state.sessions.assert_not_called()


@pytest.mark.parametrize(("method", "path"), [
    ("GET", f"{BASE}/devices"),
    ("GET", f"{BASE}/urgency"),
    ("POST", f"{BASE}/urgency"),
    ("POST", f"/api/v1/devices/{LIGHT_DEVICE_ID}/commands"),
    ("GET", "/api/v1/commands/00000000-0000-0000-0000-000000000001"),
    ("GET", f"{BASE}/environment-analysis"),
    ("POST", f"{BASE}/analytics"),
    ("GET", f"{BASE}/analytics/report"),
    ("GET", f"{BASE}/telemetry/latest/"),
    ("DELETE", f"{BASE}/analytics"),
    ("GET", "/health/live"),
    ("GET", "/openapi.json"),
    ("GET", f"/api/v1/classrooms/{uuid4()}/analytics"),
])
@pytest.mark.parametrize("host", ["127.0.0.1", "203.0.113.10"])
async def test_bridge_credential_cannot_reach_unapproved_paths_or_methods(method, path, host):
    instance = app()
    response = await send(instance, path, method=method, host=host,
                          headers={PROXY_TOKEN_HEADER: TOKEN}, body={})
    assert response.status_code == 403
    instance.state.sessions.assert_not_called()
    instance.state.environmental_analyst.generate.assert_not_awaited()


@pytest.mark.parametrize("suffix", ["telemetry/latest", "occupancy/current"])
async def test_authenticated_remote_sensor_reads_reach_existing_read_only_routes(
    monkeypatch, suffix,
):
    instance = app()
    now = datetime.now(UTC)
    if suffix == "telemetry/latest":
        reader = AsyncMock(return_value=LatestEnvironmentReading(
            classroom_exists=True,
            temperature_c=27.0,
            relative_humidity_pct=60.0,
            illuminance_lux=None,
            illuminance_adc=1001,
            motion_detected=True,
            observed_at=now,
            sample_interval_ms=2000,
        ))
        monkeypatch.setattr(
            "app.api.v1.routers.environment_analysis.get_latest_environment_reading", reader
        )
    else:
        reader = AsyncMock(return_value=CurrentOccupancy(True, 2, 0.9, now, "occupied"))
        monkeypatch.setattr("app.api.v1.routers.environment_analysis.get_current_occupancy", reader)
    response = await send(instance, f"{BASE}/{suffix}", headers={PROXY_TOKEN_HEADER: TOKEN})
    assert response.status_code == 200
    reader.assert_awaited_once()
    assert TOKEN not in response.text


async def test_authenticated_remote_analytics_reaches_reader(monkeypatch):
    instance = app()
    reader = AsyncMock(return_value=None)
    monkeypatch.setattr("app.api.v1.routers.analytics.get_analytics_report", reader)
    response = await send(instance, f"{BASE}/analytics?period=day",
                          headers={PROXY_TOKEN_HEADER: TOKEN})
    assert response.status_code == 404  # Auth passed; mocked classroom has no report.
    reader.assert_awaited_once()


async def test_report_keeps_its_independent_bearer_token_requirement():
    instance = app()
    response = await send(instance, f"{BASE}/analytics/report", method="POST",
                          headers={PROXY_TOKEN_HEADER: TOKEN}, body={})
    assert response.status_code == 401
    instance.state.sessions.assert_not_called()
    instance.state.environmental_analyst.generate.assert_not_awaited()


async def test_authenticated_remote_report_reaches_existing_reader_with_both_tokens(monkeypatch):
    instance = app()
    reader = AsyncMock(return_value=None)
    monkeypatch.setattr("app.api.v1.routers.analytics.get_analytics_report", reader)
    response = await send(instance, f"{BASE}/analytics/report", method="POST", headers={
        PROXY_TOKEN_HEADER: TOKEN, "Authorization": f"Bearer {REPORT_TOKEN}",
    }, body={})
    assert response.status_code == 404
    reader.assert_awaited_once()
    instance.state.environmental_analyst.generate.assert_not_awaited()


def test_hardware_router_guard_also_rejects_bridge_header_without_boundary_middleware():
    request = Request({
        "type": "http", "method": "POST", "path": f"{BASE}/urgency",
        "headers": [(PROXY_TOKEN_HEADER.encode(), TOKEN.encode())],
        "client": ("127.0.0.1", 1234),
        "app": SimpleNamespace(state=SimpleNamespace(local_hardware_control_enabled=True)),
    })
    with pytest.raises(HTTPException) as exc:
        require_hardware_loopback(request)
    assert exc.value.status_code == 403


@pytest.mark.parametrize("host", ["127.0.0.1", "::1"])
def test_local_direct_requests_need_no_new_settings(host):
    request = Request({
        "type": "http", "method": "GET", "path": f"{BASE}/analytics",
        "headers": [], "client": (host, 1234),
        "app": SimpleNamespace(state=SimpleNamespace()),
    })
    require_monitoring_access(request)


def test_disabled_proxy_is_the_default_and_secret_is_redacted():
    instance = Settings(_env_file=None, database_url="postgresql+asyncpg://unused")
    assert not instance.classroom_api_proxy_enabled
    assert instance.classroom_api_proxy_token is None
    assert TOKEN not in repr(settings())


async def test_configured_classroom_is_enforced(monkeypatch):
    another_classroom = uuid4()
    instance = app(classroom_api_proxy_classroom_id=another_classroom)
    blocked = await send(instance, f"{BASE}/analytics", headers={PROXY_TOKEN_HEADER: TOKEN})
    assert blocked.status_code == 403
    reader = AsyncMock(return_value=None)
    monkeypatch.setattr("app.api.v1.routers.analytics.get_analytics_report", reader)
    allowed = await send(instance, f"/api/v1/classrooms/{another_classroom}/analytics",
                         headers={PROXY_TOKEN_HEADER: TOKEN})
    assert allowed.status_code == 404
    reader.assert_awaited_once()
