from datetime import UTC, date, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException, Request
from sqlalchemy.exc import SQLAlchemyError

from app.api.v1.routers.analytics import read_analytics
from app.core.config import Settings
from app.domain.local_hardware import LOCAL_CLASSROOM_ID
from app.repositories.runtime_history import RuntimeHistory
from app.services.analytics import get_analytics_report, get_historical_comparison

SETTINGS = Settings(database_url="postgresql+asyncpg://unused")


def request(host="127.0.0.1", sessions=None):
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/analytics",
            "headers": [],
            "client": (host, 1234),
            "app": SimpleNamespace(
                state=SimpleNamespace(sessions=sessions, analytics_settings=SETTINGS)
            ),
        }
    )


@pytest.mark.asyncio
async def test_nonlocal_analytics_is_rejected_before_reading_storage():
    with pytest.raises(HTTPException) as error:
        await read_analytics(LOCAL_CLASSROOM_ID, request("203.0.113.1"))
    assert error.value.status_code == 403


@pytest.mark.asyncio
async def test_unavailable_sessions_return_503():
    with pytest.raises(HTTPException) as error:
        await read_analytics(LOCAL_CLASSROOM_ID, request())
    assert error.value.status_code == 503


@pytest.mark.asyncio
async def test_database_error_is_scoped_to_analytics():
    sessions = MagicMock()
    with (
        patch(
            "app.api.v1.routers.analytics.get_analytics_report",
            new=AsyncMock(side_effect=SQLAlchemyError("private connection details")),
        ),
        pytest.raises(HTTPException) as error,
    ):
        await read_analytics(LOCAL_CLASSROOM_ID, request(sessions=sessions))
    assert error.value.status_code == 503
    assert "private" not in str(error.value.detail)


@pytest.mark.asyncio
async def test_unknown_classroom_is_404():
    with (
        patch(
            "app.api.v1.routers.analytics.get_analytics_report", new=AsyncMock(return_value=None)
        ),
        pytest.raises(HTTPException) as error,
    ):
        await read_analytics(LOCAL_CLASSROOM_ID, request(sessions=MagicMock()))
    assert error.value.status_code == 404


@pytest.mark.asyncio
async def test_default_date_uses_classroom_timezone_and_needs_no_ai():
    session = MagicMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = "Asia/Kuala_Lumpur"
    session.execute = AsyncMock(return_value=result)
    history = RuntimeHistory(True, None, [], "Asia/Kuala_Lumpur")
    now = datetime(2026, 10, 25, 18, tzinfo=UTC)  # already 26 October locally
    with patch("app.services.analytics.load_runtime_history", new=AsyncMock(return_value=history)):
        report = await get_analytics_report(session, LOCAL_CLASSROOM_ID, SETTINGS, now=now)
    assert report.period_start == datetime(2026, 10, 25, 16, tzinfo=UTC)
    assert report.period_elapsed_seconds == 7200
    assert report.total_energy.final_savings_pct is None
    assert report.timezone == "Asia/Kuala_Lumpur"


@pytest.mark.asyncio
async def test_selected_week_is_calendar_week_and_has_seven_day_baseline():
    session = MagicMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = "Asia/Kuala_Lumpur"
    session.execute = AsyncMock(return_value=result)
    history = RuntimeHistory(True, None, [], "Asia/Kuala_Lumpur")
    with patch("app.services.analytics.load_runtime_history", new=AsyncMock(return_value=history)):
        report = await get_analytics_report(
            session,
            LOCAL_CLASSROOM_ID,
            SETTINGS,
            date(2026, 10, 28),
            "week",
            datetime(2026, 11, 10, tzinfo=UTC),
        )
    assert report.calendar_days == 7
    assert len(report.devices[0].daily) == 7
    assert report.total_energy.period_baseline_kwh == pytest.approx(25.68 * 7)


def test_registered_route_is_additive_and_has_no_command_mutation():
    from app.main import app

    paths = app.openapi()["paths"]
    assert set(paths["/api/v1/classrooms/{classroom_id}/analytics"]) == {"get"}
    assert "/api/v1/classrooms/{classroom_id}/devices" in paths
    assert "/api/v1/classrooms/{classroom_id}/telemetry/latest" in paths


@pytest.mark.asyncio
async def test_previous_day_comparison_withholds_changes_for_partial_periods():
    from app.services.energy_analytics import build_analytics_report

    start = datetime(2026, 10, 25, 16, tzinfo=UTC)
    end = datetime(2026, 10, 26, 16, tzinfo=UTC)
    history = RuntimeHistory(True, start, [], "Asia/Kuala_Lumpur")
    current = build_analytics_report(
        history=history,
        classroom_id=LOCAL_CLASSROOM_ID,
        start=start,
        end=end,
        now=end,
        period="day",
        powers={"lighting": 10, "fan": 60, "air_conditioner": 1000},
    )
    previous = current.model_copy(deep=True)
    previous.devices[0].runtime.coverage_pct = 20
    with patch("app.services.analytics.get_analytics_report", new=AsyncMock(return_value=previous)):
        comparison = await get_historical_comparison(MagicMock(), current, SETTINGS)
    assert comparison["available"]
    assert not comparison["both_periods_complete"]
    assert comparison["estimated_energy_change_kwh"] is None
    assert comparison["previous_devices"][0]["runtime"]["coverage_pct"] == 20


@pytest.mark.asyncio
async def test_history_comparison_never_fabricates_previous_usage():
    from app.services.energy_analytics import build_analytics_report

    start = datetime(2026, 10, 25, 16, tzinfo=UTC)
    end = datetime(2026, 10, 26, 16, tzinfo=UTC)
    current = build_analytics_report(
        history=RuntimeHistory(True, None, [], "Asia/Kuala_Lumpur"),
        classroom_id=LOCAL_CLASSROOM_ID,
        start=start,
        end=end,
        now=end,
        period="day",
        powers={"lighting": 10, "fan": 60, "air_conditioner": 1000},
    )
    loader = AsyncMock()
    with patch("app.services.analytics.get_analytics_report", new=loader):
        comparison = await get_historical_comparison(MagicMock(), current, SETTINGS)
    assert not comparison["available"]
    loader.assert_not_awaited()
