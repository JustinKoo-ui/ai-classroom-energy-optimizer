"""Persistence adapter for validated environment telemetry."""

from __future__ import annotations

from uuid import UUID, uuid4

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.mqtt import MqttEnvelope, EnvironmentTelemetryPayload
from app.core.db import environment_readings


async def insert_environment_reading(
    session: AsyncSession,
    envelope: MqttEnvelope[EnvironmentTelemetryPayload],
) -> bool:
    """Persist one reading; return false when its message_id was already seen."""
    payload = envelope.payload
    statement = insert(environment_readings).values(
        id=uuid4(),
        message_id=envelope.message_id,
        classroom_id=envelope.classroom_id,
        sensor_id=payload.sensor_id,
        temperature_c=payload.temperature_c,
        relative_humidity_pct=payload.relative_humidity_pct,
        illuminance_lux=payload.illuminance_lux,
        sample_interval_ms=payload.sample_interval_ms,
        quality={},
        occurred_at=envelope.occurred_at,
    ).on_conflict_do_nothing(index_elements=[environment_readings.c.message_id])
    result = await session.execute(statement)
    await session.commit()
    return result.rowcount == 1
