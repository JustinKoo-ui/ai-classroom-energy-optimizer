"""Local-development REST endpoint for stored environment analysis."""

from __future__ import annotations

import ipaddress
from datetime import UTC, datetime, timedelta
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.repositories.environment import (
    MetricAggregate,
    get_environment_reading_aggregate,
    get_latest_environment_reading,
)
from app.repositories.occupancy import get_current_occupancy
from app.schemas.common import Freshness, OccupancyState, OccupancyStatus
from app.schemas.environment_analysis import EnvironmentAnalysis, LatestEnvironmentTelemetry
from app.services.environment_analysis import build_environment_analysis

router = APIRouter(prefix="/api/v1/classrooms", tags=["environment analysis"])
OCCUPANCY_STALE_AFTER = timedelta(seconds=15)


def _require_loopback(request: Request) -> None:
    client_host = request.client.host if request.client is not None else ""
    try:
        is_local_client = ipaddress.ip_address(client_host).is_loopback
    except ValueError:
        is_local_client = False
    if not is_local_client:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Environment telemetry is restricted to localhost during the demo phase.",
        )


def _get_sessions(request: Request) -> async_sessionmaker[AsyncSession]:
    sessions: async_sessionmaker[AsyncSession] | None = getattr(
        request.app.state,
        "sessions",
        None,
    )
    if sessions is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database is not available.",
        )
    return sessions


@router.get("/{classroom_id}/telemetry/latest", response_model=LatestEnvironmentTelemetry)
async def read_latest_environment_telemetry(
    classroom_id: UUID,
    request: Request,
) -> LatestEnvironmentTelemetry:
    """Return the latest persisted sensor values without deriving occupancy."""
    _require_loopback(request)
    analyzed_at = datetime.now(UTC)
    sessions = _get_sessions(request)
    async with sessions() as session:
        reading = await get_latest_environment_reading(session, classroom_id)
    if reading is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Classroom was not found.",
        )
    freshness = (
        "unknown"
        if reading.observed_at is None
        else "fresh"
        if analyzed_at - reading.observed_at
        <= timedelta(milliseconds=max(reading.sample_interval_ms or 5000, 2000) * 3)
        else "stale"
    )
    return LatestEnvironmentTelemetry(
        classroom_id=classroom_id,
        temperature_c=reading.temperature_c,
        relative_humidity_pct=reading.relative_humidity_pct,
        illuminance_lux=reading.illuminance_lux,
        illuminance_adc=reading.illuminance_adc,
        motion_detected=reading.motion_detected,
        sample_interval_ms=reading.sample_interval_ms,
        observed_at=reading.observed_at,
        freshness=freshness,
    )


@router.get("/{classroom_id}/occupancy/current", response_model=OccupancyState)
async def read_current_occupancy(
    classroom_id: UUID,
    request: Request,
) -> OccupancyState:
    """Return the latest validated occupancy, hiding counts once they are stale."""
    _require_loopback(request)
    sessions = _get_sessions(request)
    async with sessions() as session:
        reading = await get_current_occupancy(session, classroom_id)
    if not reading.classroom_exists:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Classroom was not found.",
        )

    now = datetime.now(UTC)
    age = now - reading.observed_at if reading.observed_at is not None else None
    freshness = (
        Freshness.UNKNOWN
        if age is None or age < timedelta(0)
        else Freshness.FRESH
        if age <= OCCUPANCY_STALE_AFTER
        else Freshness.STALE
    )
    if freshness is not Freshness.FRESH:
        return OccupancyState(
            status=OccupancyStatus.UNKNOWN,
            count=None,
            confidence=None,
            observed_at=reading.observed_at,
            freshness=freshness,
        )

    return OccupancyState(
        status=OccupancyStatus(reading.status),
        count=reading.person_count,
        confidence=reading.confidence,
        observed_at=reading.observed_at,
        freshness=freshness,
    )


@router.get("/{classroom_id}/environment-analysis", response_model=EnvironmentAnalysis)
async def read_environment_analysis(
    classroom_id: UUID,
    request: Request,
) -> EnvironmentAnalysis:
    """Return deterministic analysis of stored environment telemetry."""
    _require_loopback(request)
    sessions = _get_sessions(request)

    analyzed_at = datetime.now(UTC)
    async with sessions() as session:
        aggregate = await get_environment_reading_aggregate(session, classroom_id, analyzed_at)
    if aggregate is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Classroom was not found.",
        )

    def metric_values(
        metric: MetricAggregate,
    ) -> tuple[float | None, float | None, float | None, float | None, float | None]:
        return (
            metric.latest,
            metric.average,
            metric.minimum,
            metric.maximum,
            metric.previous,
        )

    return build_environment_analysis(
        classroom_id=classroom_id,
        reading_count=aggregate.reading_count,
        analyzed_at=analyzed_at,
        latest_observed_at=aggregate.latest_observed_at,
        temperature=metric_values(aggregate.temperature),
        humidity=metric_values(aggregate.humidity),
        illuminance=metric_values(aggregate.illuminance),
    )
