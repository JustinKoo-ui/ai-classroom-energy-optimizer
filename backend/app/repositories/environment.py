"""Persistence adapter for validated environment telemetry."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import environment_readings
from app.schemas.mqtt import (
    EnvironmentTelemetryPayload,
    EnvironmentTelemetryPayloadV2,
    MqttEnvelope,
    MqttEnvelopeV2,
)


@dataclass(frozen=True)
class MetricAggregate:
    latest: float | None
    average: float | None
    minimum: float | None
    maximum: float | None
    previous: float | None


@dataclass(frozen=True)
class EnvironmentReadingAggregate:
    reading_count: int
    latest_observed_at: datetime | None
    temperature: MetricAggregate
    humidity: MetricAggregate
    illuminance: MetricAggregate


@dataclass(frozen=True)
class LatestEnvironmentReading:
    classroom_exists: bool
    temperature_c: float | None
    relative_humidity_pct: float | None
    illuminance_lux: float | None
    illuminance_adc: int | None
    motion_detected: bool | None
    observed_at: datetime | None
    sample_interval_ms: int | None


async def insert_environment_reading(
    session: AsyncSession,
    envelope: (
        MqttEnvelope[EnvironmentTelemetryPayload]
        | MqttEnvelopeV2[EnvironmentTelemetryPayloadV2]
    ),
) -> bool:
    """Persist one reading; return false when its message_id was already seen."""
    payload = envelope.payload
    if isinstance(payload, EnvironmentTelemetryPayloadV2):
        illuminance_lux = None
        illuminance_adc = payload.illuminance_adc
        motion_detected = payload.motion_detected
    else:
        illuminance_lux = payload.illuminance_lux
        illuminance_adc = None
        motion_detected = None
    statement = (
        insert(environment_readings)
        .values(
            id=uuid4(),
            message_id=envelope.message_id,
            classroom_id=envelope.classroom_id,
            sensor_id=payload.sensor_id,
            temperature_c=payload.temperature_c,
            relative_humidity_pct=payload.relative_humidity_pct,
            illuminance_lux=illuminance_lux,
            illuminance_adc=illuminance_adc,
            motion_detected=motion_detected,
            sample_interval_ms=payload.sample_interval_ms,
            quality={},
            occurred_at=envelope.occurred_at,
        )
        .on_conflict_do_nothing(index_elements=[environment_readings.c.message_id])
        .returning(environment_readings.c.message_id)
    )
    result = await session.execute(statement)
    await session.commit()
    return result.scalar_one_or_none() is not None


async def get_latest_environment_reading(
    session: AsyncSession,
    classroom_id: UUID,
) -> LatestEnvironmentReading | None:
    result = await session.execute(
        text(
            """
            SELECT
                EXISTS (SELECT 1 FROM classrooms WHERE id = :classroom_id) AS classroom_exists,
                reading.temperature_c,
                reading.relative_humidity_pct,
                reading.illuminance_lux,
                reading.illuminance_adc,
                reading.motion_detected,
                reading.sample_interval_ms,
                reading.occurred_at AS observed_at
            FROM (SELECT 1) AS singleton
            LEFT JOIN LATERAL (
                SELECT *
                FROM environment_readings
                WHERE classroom_id = :classroom_id
                ORDER BY occurred_at DESC, message_id DESC
                LIMIT 1
            ) AS reading ON true
            """
        ),
        {"classroom_id": classroom_id},
    )
    row: Any = result.mappings().one()
    if not row["classroom_exists"]:
        return None
    return LatestEnvironmentReading(
        classroom_exists=True,
        temperature_c=_as_float(row["temperature_c"]),
        relative_humidity_pct=_as_float(row["relative_humidity_pct"]),
        illuminance_lux=_as_float(row["illuminance_lux"]),
        illuminance_adc=row["illuminance_adc"],
        motion_detected=row["motion_detected"],
        observed_at=row["observed_at"],
        sample_interval_ms=row["sample_interval_ms"],
    )


async def get_environment_reading_aggregate(
    session: AsyncSession,
    classroom_id: UUID,
    analyzed_at: datetime,
) -> EnvironmentReadingAggregate | None:
    """Return 24-hour reading aggregates, or None if the classroom does not exist."""
    result = await session.execute(
        text(
            """
            WITH classroom AS (
                SELECT id FROM classrooms WHERE id = :classroom_id
            ),
            window_readings AS (
                SELECT reading.*
                FROM environment_readings AS reading
                JOIN classroom ON classroom.id = reading.classroom_id
                WHERE reading.occurred_at >= :window_start
                  AND reading.occurred_at <= :analyzed_at
            ),
            stats AS (
                SELECT
                    COUNT(*) AS reading_count,
                    AVG(temperature_c) AS temperature_average,
                    MIN(temperature_c) AS temperature_minimum,
                    MAX(temperature_c) AS temperature_maximum,
                    AVG(relative_humidity_pct) AS humidity_average,
                    MIN(relative_humidity_pct) AS humidity_minimum,
                    MAX(relative_humidity_pct) AS humidity_maximum,
                    AVG(illuminance_lux) AS illuminance_average,
                    MIN(illuminance_lux) AS illuminance_minimum,
                    MAX(illuminance_lux) AS illuminance_maximum
                FROM window_readings
            )
            SELECT
                EXISTS (SELECT 1 FROM classroom) AS classroom_exists,
                stats.*,
                (SELECT occurred_at FROM window_readings
                 ORDER BY occurred_at DESC, message_id DESC LIMIT 1) AS latest_observed_at,
                (SELECT temperature_c FROM window_readings
                 ORDER BY occurred_at DESC, message_id DESC LIMIT 1) AS temperature_latest,
                (SELECT temperature_c FROM window_readings
                 ORDER BY occurred_at DESC, message_id DESC OFFSET 1 LIMIT 1)
                    AS temperature_previous,
                (SELECT relative_humidity_pct FROM window_readings
                 ORDER BY occurred_at DESC, message_id DESC LIMIT 1) AS humidity_latest,
                (SELECT relative_humidity_pct FROM window_readings
                 ORDER BY occurred_at DESC, message_id DESC OFFSET 1 LIMIT 1) AS humidity_previous,
                (SELECT illuminance_lux FROM window_readings
                 ORDER BY occurred_at DESC, message_id DESC LIMIT 1) AS illuminance_latest,
                (SELECT illuminance_lux FROM window_readings
                 ORDER BY occurred_at DESC, message_id DESC OFFSET 1 LIMIT 1)
                    AS illuminance_previous
            FROM stats
            """
        ),
        {
            "classroom_id": classroom_id,
            "window_start": analyzed_at - timedelta(hours=24),
            "analyzed_at": analyzed_at,
        },
    )
    row: Any = result.mappings().one()
    if not row["classroom_exists"]:
        return None

    return EnvironmentReadingAggregate(
        reading_count=row["reading_count"],
        latest_observed_at=row["latest_observed_at"],
        temperature=MetricAggregate(
            latest=_as_float(row["temperature_latest"]),
            average=_as_float(row["temperature_average"]),
            minimum=_as_float(row["temperature_minimum"]),
            maximum=_as_float(row["temperature_maximum"]),
            previous=_as_float(row["temperature_previous"]),
        ),
        humidity=MetricAggregate(
            latest=_as_float(row["humidity_latest"]),
            average=_as_float(row["humidity_average"]),
            minimum=_as_float(row["humidity_minimum"]),
            maximum=_as_float(row["humidity_maximum"]),
            previous=_as_float(row["humidity_previous"]),
        ),
        illuminance=MetricAggregate(
            latest=_as_float(row["illuminance_latest"]),
            average=_as_float(row["illuminance_average"]),
            minimum=_as_float(row["illuminance_minimum"]),
            maximum=_as_float(row["illuminance_maximum"]),
            previous=_as_float(row["illuminance_previous"]),
        ),
    )


def _as_float(value: Any) -> float | None:
    return float(value) if value is not None else None
