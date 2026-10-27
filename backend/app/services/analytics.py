"""Load only persisted observation evidence for the deterministic report."""

from datetime import UTC, date, datetime, timedelta
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.domain.runtime import Period, period_bounds
from app.repositories.runtime_history import load_runtime_history
from app.schemas.analytics import AnalyticsReport
from app.services.energy_analytics import build_analytics_report


async def get_historical_comparison(
    session: AsyncSession,
    report: AnalyticsReport,
    settings: Settings,
) -> dict[str, Any]:
    """Supply previous-day metrics only when recorded evidence exists."""
    if report.period != "day":
        return {"available": False, "reason": "Use supplied daily buckets within this period."}
    if report.tracking_started_at is None:
        return {"available": False, "reason": "No reliable runtime tracking history."}
    local_day = report.period_start.astimezone(ZoneInfo(report.timezone)).date()
    if local_day == date.min:
        return {"available": False, "reason": "No earlier calendar day."}
    previous = await get_analytics_report(
        session,
        report.classroom_id,
        settings,
        local_day - timedelta(days=1),
        "day",
        report.as_of,
    )
    if previous is None or not any(d.runtime.coverage_pct > 0 for d in previous.devices):
        return {"available": False, "reason": "No previous-day ON/OFF observation coverage."}
    comparable = not report.is_partial and not previous.is_partial
    return {
        "available": True,
        "previous_period_start": previous.period_start.isoformat(),
        "previous_period_end": previous.period_end.isoformat(),
        "both_periods_complete": comparable,
        "previous_total_energy": previous.total_energy.model_dump(mode="json"),
        "previous_devices": [
            {
                "device": d.key,
                "runtime": d.runtime.model_dump(mode="json"),
                "energy": d.energy.model_dump(mode="json"),
            }
            for d in previous.devices
        ],
        "estimated_energy_change_kwh": (
            report.total_energy.estimated_energy_kwh - previous.total_energy.estimated_energy_kwh
            if comparable
            else None
        ),
        "limitation": "Only complete periods support like-for-like daily differences; "
        "these remain assumed-wattage estimates, not measured savings.",
    }


async def get_analytics_report(
    session: AsyncSession,
    classroom_id: UUID,
    settings: Settings,
    selected_date: date | None = None,
    period: Period = "day",
    now: datetime | None = None,
) -> AnalyticsReport | None:
    now = now or datetime.now(UTC)
    result = await session.execute(
        text("SELECT timezone FROM classrooms WHERE id = :id"),
        {"id": classroom_id},
    )
    timezone = result.scalar_one_or_none()
    if timezone is None:
        return None
    zone = ZoneInfo(timezone)
    selected_date = selected_date or now.astimezone(zone).date()
    start, end = period_bounds(selected_date, period, timezone)
    history = await load_runtime_history(
        session,
        classroom_id,
        start,
        end,
        settings.analytics_stale_after_seconds,
    )
    return build_analytics_report(
        history=history,
        classroom_id=classroom_id,
        start=start,
        end=end,
        now=now,
        period=period,
        stale_after_seconds=settings.analytics_stale_after_seconds,
        powers={
            "lighting": settings.analytics_light_power_w,
            "fan": settings.analytics_fan_power_w,
            "air_conditioner": settings.analytics_ac_power_w,
        },
    )
