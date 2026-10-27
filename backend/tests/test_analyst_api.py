"""Real ASGI route validation with simulated storage/provider services."""

from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy.exc import SQLAlchemyError

from app.api.v1.routers.analytics import router
from app.core.config import Settings
from app.domain.local_hardware import LOCAL_CLASSROOM_ID
from app.domain.runtime import period_bounds
from app.repositories.runtime_history import RuntimeHistory
from app.schemas.analyst import AnalystResponse
from app.services.energy_analytics import build_analytics_report

TOKEN = "local-report-access-token"
PATH = f"/api/v1/classrooms/{LOCAL_CLASSROOM_ID}/analytics/report"


def app(token=TOKEN):
    instance = FastAPI()
    instance.include_router(router)
    instance.state.analytics_settings = Settings(
        _env_file=None,
        database_url="postgresql+asyncpg://unused",
        analyst_access_token=token,
        analyst_api_key="private-provider-key",
    )
    instance.state.sessions = MagicMock()
    instance.state.environmental_analyst = SimpleNamespace(
        generate=AsyncMock(
            return_value=AnalystResponse(status="unavailable", message="Provider not configured.")
        )
    )
    return instance


def numerical_report():
    start, end = period_bounds(date(2026, 10, 25), "day", "Asia/Kuala_Lumpur")
    return build_analytics_report(
        history=RuntimeHistory(True, None, [], "Asia/Kuala_Lumpur"),
        classroom_id=LOCAL_CLASSROOM_ID,
        start=start,
        end=end,
        now=end,
        period="day",
        powers={"lighting": 10, "fan": 60, "air_conditioner": 1000},
    )


async def post(instance, body=None, token=TOKEN, host="127.0.0.1"):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=instance, client=(host, 1234)),
        base_url="http://test",
    ) as client:
        return await client.post(
            PATH, json=body or {}, headers={"Authorization": f"Bearer {token}"} if token else {}
        )


@pytest.mark.parametrize("token", [None, "wrong-token", "private-provider-key"])
async def test_report_token_is_required_before_any_storage_or_provider_access(token):
    instance = app()
    response = await post(instance, token=token)
    assert response.status_code == 401
    instance.state.sessions.assert_not_called()
    instance.state.environmental_analyst.generate.assert_not_awaited()
    assert "private-provider-key" not in response.text


async def test_remote_client_cannot_generate_even_with_token():
    instance = app()
    response = await post(instance, host="203.0.113.2")
    assert response.status_code == 403
    instance.state.sessions.assert_not_called()


async def test_missing_server_access_token_is_scoped_unavailability():
    response = await post(app(token=None))
    assert response.status_code == 503


@pytest.mark.parametrize(
    "body",
    [
        {"date": "not-a-date"},
        {"period": "year"},
        {"tools": ["mqtt_publish"]},
        {"refresh": True, "api_key": "must-never-accept-browser-key"},
    ],
)
async def test_request_validation_rejects_invalid_input_and_control_fields(body):
    instance = app()
    response = await post(instance, body=body)
    assert response.status_code == 422
    instance.state.environmental_analyst.generate.assert_not_awaited()


async def test_authorized_report_uses_server_metrics_and_aggregate_context():
    instance = app()
    numerical = numerical_report()
    context = {"occupancy": {"valid_sample_count": 0}, "limitations": ["No occupancy samples."]}
    with (
        patch(
            "app.api.v1.routers.analytics.get_analytics_report",
            new=AsyncMock(return_value=numerical),
        ),
        patch(
            "app.api.v1.routers.analytics.load_analyst_context", new=AsyncMock(return_value=context)
        ),
    ):
        response = await post(instance, {"date": "2026-10-25", "refresh": True})
    assert response.status_code == 200
    instance.state.environmental_analyst.generate.assert_awaited_once_with(
        numerical, context, refresh=True
    )
    assert "private-provider-key" not in response.text
    assert TOKEN not in response.text


async def test_context_storage_failure_does_not_remove_numerical_endpoint():
    instance = app()
    numerical = numerical_report()
    with (
        patch(
            "app.api.v1.routers.analytics.get_analytics_report",
            new=AsyncMock(return_value=numerical),
        ),
        patch(
            "app.api.v1.routers.analytics.load_analyst_context",
            new=AsyncMock(side_effect=SQLAlchemyError()),
        ),
    ):
        response = await post(instance)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=instance),
            base_url="http://test",
        ) as client:
            metrics = await client.get(PATH.removesuffix("/report"))
    assert response.status_code == 200
    assert response.json()["status"] == "unavailable"
    assert metrics.status_code == 200
    assert metrics.json()["total_energy"]["daily_24h_baseline_kwh"] == pytest.approx(25.68)
    instance.state.environmental_analyst.generate.assert_not_awaited()
