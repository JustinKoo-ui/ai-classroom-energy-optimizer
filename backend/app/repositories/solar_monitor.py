"""Persistence and freshness for the read-only prototype monitoring feed."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Literal
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.schemas.classroom_telemetry import (
    ClassroomEnergyPoint,
    ClassroomMeterPoint,
    ClassroomTelemetryReading,
)
from app.schemas.common import Freshness
from app.schemas.solar_monitor import (
    PrototypeHealth,
    PrototypeSnapshot,
    SolarAvailability,
    SolarHealth,
    SolarReading,
    SolarStatusEnvelope,
    SolarTelemetryEnvelope,
)

Availability = Literal["online", "offline", "unknown"]
RECENT_READING_LIMIT = 120
RELAY_FRESHNESS_SECONDS = 15
SOLAR_CLOCK_TOLERANCE = timedelta(seconds=30)


async def insert_solar_message(
    session: AsyncSession,
    envelope: SolarTelemetryEnvelope | SolarStatusEnvelope,
    received_at: datetime,
) -> bool:
    """Store a validated event once, scoped to a classroom in the expected site."""
    result = await session.execute(
        text(
            """
            INSERT INTO solar_tracker_messages (
                message_id, site_id, classroom_id, producer_id, message_kind,
                occurred_at, received_at, payload
            )
            SELECT :message_id, classroom.site_id, classroom.id, :producer_id, :kind,
                   :occurred_at, :received_at, CAST(:payload AS jsonb)
            FROM classrooms AS classroom
            WHERE classroom.id = :classroom_id AND classroom.site_id = :site_id
            ON CONFLICT (message_id) DO NOTHING
            RETURNING message_id
            """
        ),
        {
            "message_id": envelope.message_id,
            "site_id": envelope.site_id,
            "classroom_id": envelope.classroom_id,
            "producer_id": envelope.producer_id,
            "kind": "telemetry" if isinstance(envelope, SolarTelemetryEnvelope) else "status",
            "occurred_at": envelope.occurred_at,
            "received_at": received_at,
            "payload": envelope.payload.model_dump_json(),
        },
    )
    await session.commit()
    return result.scalar_one_or_none() is not None


async def record_relay_receipt(
    session: AsyncSession, classroom_id: UUID, occurred_at: datetime, received_at: datetime
) -> None:
    """Track non-retained relay heartbeat receipts independently of availability."""
    await session.execute(
        text(
            """
            INSERT INTO relay_monitor_receipts (classroom_id, occurred_at, received_at)
            VALUES (:classroom_id, :occurred_at, :received_at)
            ON CONFLICT (classroom_id) DO UPDATE
            SET occurred_at = EXCLUDED.occurred_at, received_at = EXCLUDED.received_at
            WHERE EXCLUDED.occurred_at > relay_monitor_receipts.occurred_at
            """
        ),
        {"classroom_id": classroom_id, "occurred_at": occurred_at, "received_at": received_at},
    )
    await session.commit()


async def read_prototype_snapshot(
    session: AsyncSession,
    classroom_id: UUID,
    settings: Settings,
    *,
    broker_connected: bool,
    broker_connected_at: datetime | None,
    include_history: bool,
) -> PrototypeSnapshot | None:
    scope = {
        "classroom_id": classroom_id,
        "solar_site_id": settings.solar_site_id,
        "solar_producer_id": settings.solar_producer_id,
    }
    result = await session.execute(
        text(
            """
            SELECT recent_status.payload AS solar_status,
                   recent_telemetry.readings AS solar_readings,
                   relay_reading.received_at AS relay_received_at,
                   relay_reading.occurred_at AS relay_occurred_at,
                   relay_state.any_online AS relay_online,
                   relay_state.all_offline AS relay_offline,
                   to_jsonb(classroom_reading) AS classroom_telemetry,
                   classroom_history.energy_points AS energy_history,
                   classroom_history.meter_points AS classroom_meter_history
            FROM classrooms AS classroom
            LEFT JOIN LATERAL (
                SELECT payload
                FROM solar_tracker_messages
                WHERE classroom_id = classroom.id AND site_id = :solar_site_id
                  AND producer_id = :solar_producer_id AND message_kind = 'status'
                ORDER BY received_at DESC, message_id DESC
                LIMIT 1
            ) AS recent_status ON TRUE
            CROSS JOIN LATERAL (
                SELECT jsonb_agg(
                    to_jsonb(reading) ORDER BY reading.received_at DESC, reading.message_id DESC
                ) AS readings
                FROM (
                    SELECT message_id, received_at, occurred_at, payload
                    FROM solar_tracker_messages
                    WHERE classroom_id = classroom.id AND site_id = :solar_site_id
                      AND producer_id = :solar_producer_id AND message_kind = 'telemetry'
                    ORDER BY received_at DESC, message_id DESC
                    LIMIT :row_limit
                ) AS reading
            ) AS recent_telemetry
            LEFT JOIN LATERAL (
                SELECT occurred_at, received_at
                FROM environment_readings
                WHERE classroom_id = classroom.id AND sensor_id = :relay_sensor_id
                ORDER BY received_at DESC
                LIMIT 1
            ) AS relay_environment ON TRUE
            LEFT JOIN LATERAL (
                SELECT message_id, received_at, device_occurred_at AS occurred_at,
                       classroom_telemetry AS payload
                FROM environment_readings AS reading
                WHERE classroom_id = classroom.id AND sensor_id = :relay_sensor_id
                  AND classroom_telemetry IS NOT NULL
                ORDER BY reading.occurred_at DESC, received_at DESC, message_id DESC
                LIMIT 1
            ) AS classroom_reading ON TRUE
            CROSS JOIN LATERAL (
                SELECT jsonb_agg(
                           jsonb_build_object('received_at', point.received_at,
                                              'energy_wh_total', point.energy_wh_total)
                           ORDER BY point.received_at
                       ) FILTER (WHERE jsonb_typeof(point.energy_wh_total) = 'number')
                           AS energy_points,
                       jsonb_agg(to_jsonb(point) ORDER BY point.received_at) AS meter_points
                FROM (
                    SELECT received_at,
                           payload -> 'boot_id' AS boot_id,
                           CASE WHEN ina219_available THEN payload -> 'active_power_w' END
                               AS active_power_w,
                           CASE WHEN ina219_available THEN payload -> 'energy_wh_total' END
                               AS energy_wh_total
                    FROM (
                        SELECT received_at, classroom_telemetry AS payload,
                               classroom_telemetry #> '{sensors,ina219_available}' = 'true'::jsonb
                                   AS ina219_available
                        FROM environment_readings
                        WHERE classroom_id = classroom.id AND sensor_id = :relay_sensor_id
                          AND classroom_telemetry IS NOT NULL
                        ORDER BY occurred_at DESC, received_at DESC, message_id DESC
                        LIMIT :classroom_history_limit
                    ) AS recent_classroom
                ) AS point
            ) AS classroom_history
            LEFT JOIN relay_monitor_receipts AS relay_report
                ON relay_report.classroom_id = classroom.id
            CROSS JOIN LATERAL (
                SELECT occurred_at, received_at
                FROM (
                    VALUES (relay_report.occurred_at, relay_report.received_at),
                           (relay_environment.occurred_at, relay_environment.received_at)
                ) AS receipts (occurred_at, received_at)
                ORDER BY received_at DESC NULLS LAST
                LIMIT 1
            ) AS relay_reading
            CROSS JOIN LATERAL (
                SELECT bool_or(state.operational_status = 'online') AS any_online,
                       bool_and(state.operational_status = 'offline') AS all_offline
                FROM devices AS device
                JOIN device_states AS state ON state.device_id = device.id
                WHERE device.classroom_id = classroom.id AND device.enabled
                  AND device.hardware_uid LIKE 'aceo-local-relay-%'
            ) AS relay_state
            WHERE classroom.id = :classroom_id
            """
        ),
        {
            **scope,
            "relay_sensor_id": settings.relay_monitor_sensor_id,
            "row_limit": RECENT_READING_LIMIT if include_history else 1,
            "classroom_history_limit": RECENT_READING_LIMIT if include_history else 0,
        },
    )
    state = result.mappings().one_or_none()
    if state is None:
        return None

    readings = [SolarReading.model_validate(row) for row in state["solar_readings"] or []]
    solar_reading = readings[0] if readings else None
    solar_status = (
        SolarAvailability.model_validate(state["solar_status"]) if state["solar_status"] else None
    )
    now = datetime.now(UTC)
    solar_received_at = solar_reading.received_at if solar_reading else None
    solar_occurred_at = solar_reading.occurred_at if solar_reading else None
    relay_received_at = state["relay_received_at"]
    relay_occurred_at = state["relay_occurred_at"]
    solar_freshness = reading_freshness(
        solar_received_at,
        solar_occurred_at,
        now=now,
        connected_at=broker_connected_at,
        stale_after=timedelta(seconds=settings.solar_stale_after_seconds),
        broker_connected=broker_connected,
        clock_tolerance=SOLAR_CLOCK_TOLERANCE,
    )
    relay_freshness = reading_freshness(
        relay_received_at,
        relay_occurred_at,
        now=now,
        connected_at=broker_connected_at,
        stale_after=timedelta(seconds=RELAY_FRESHNESS_SECONDS),
        broker_connected=broker_connected,
    )
    solar_availability: Availability = solar_status.status if solar_status else "unknown"
    relay_availability: Availability = (
        "online" if state["relay_online"] else "offline" if state["relay_offline"] else "unknown"
    )
    if solar_status and solar_reading and solar_status.boot_id != solar_reading.payload.boot_id:
        solar_freshness = Freshness.STALE
    if solar_availability == "offline" and solar_reading:
        solar_freshness = Freshness.STALE
    if relay_availability == "offline" and relay_received_at:
        relay_freshness = Freshness.STALE
    classroom_telemetry = (
        ClassroomTelemetryReading.model_validate(state["classroom_telemetry"])
        if state["classroom_telemetry"] is not None
        else None
    )
    classroom_freshness = reading_freshness(
        classroom_telemetry.received_at if classroom_telemetry else None,
        classroom_telemetry.occurred_at if classroom_telemetry else None,
        now=now,
        connected_at=broker_connected_at,
        stale_after=timedelta(seconds=RELAY_FRESHNESS_SECONDS),
        broker_connected=broker_connected,
    )
    if classroom_telemetry and relay_availability == "offline":
        classroom_freshness = Freshness.STALE
    return PrototypeSnapshot(
        broker_connected=broker_connected,
        solar=SolarHealth(
            availability=solar_availability if broker_connected else "unknown",
            freshness=solar_freshness,
            last_received_at=solar_received_at,
            telemetry=solar_reading,
        ),
        relay=PrototypeHealth(
            availability=relay_availability if broker_connected else "unknown",
            freshness=relay_freshness,
            last_received_at=relay_received_at,
        ),
        history=list(reversed(readings)) if include_history else None,
        classroom_telemetry=classroom_telemetry,
        classroom_telemetry_freshness=classroom_freshness,
        energy_history=(
            [ClassroomEnergyPoint.model_validate(point) for point in state["energy_history"] or []]
            if include_history
            else None
        ),
        classroom_meter_history=(
            [
                ClassroomMeterPoint.model_validate(point)
                for point in state["classroom_meter_history"] or []
            ]
            if include_history
            else None
        ),
    )


def reading_freshness(
    received_at: datetime | None,
    occurred_at: datetime | None,
    *,
    now: datetime,
    connected_at: datetime | None,
    stale_after: timedelta,
    broker_connected: bool,
    clock_tolerance: timedelta = timedelta(0),
) -> Freshness:
    if received_at is None:
        return Freshness.UNKNOWN
    if (
        not broker_connected
        or (connected_at is not None and received_at < connected_at)
        or now - received_at > stale_after
        or (occurred_at is not None and now - occurred_at > stale_after + clock_tolerance)
    ):
        return Freshness.STALE
    return Freshness.FRESH
