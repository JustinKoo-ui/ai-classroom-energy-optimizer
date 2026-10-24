"""Persistence for device reports and command acknowledgements."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.mqtt import (
    AcknowledgementStatus,
    AvailabilityPayload,
    CommandAcknowledgementPayload,
    DeviceStatePayload,
)


async def persist_device_state(
    session: AsyncSession,
    *,
    site_id: UUID,
    classroom_id: UUID,
    message_id: UUID,
    retained: bool,
    occurred_at: datetime,
    payload: DeviceStatePayload,
) -> None:
    await _assert_device_identity(session, payload.device_id, site_id, classroom_id)
    state_json = json.dumps(payload.state)
    faults_json = payload.fault_codes
    await session.execute(
        text(
            """
            INSERT INTO device_states (
                device_id, operational_status, reported_state, fault_codes,
                state_version, last_seen_at, reported_at
            )
            VALUES (
                :device_id,
                CASE WHEN :retained THEN 'registered'::device_operational_status
                     ELSE 'online'::device_operational_status END,
                CAST(:state AS jsonb), :fault_codes,
                :state_version, :occurred_at, :occurred_at
            )
            ON CONFLICT (device_id) DO UPDATE SET
                operational_status = CASE
                    WHEN :retained THEN device_states.operational_status
                    WHEN device_states.last_seen_at IS NULL
                      OR EXCLUDED.last_seen_at >= device_states.last_seen_at
                    THEN 'online' ELSE device_states.operational_status END,
                reported_state = CASE
                    WHEN EXCLUDED.state_version >= device_states.state_version
                    THEN EXCLUDED.reported_state ELSE device_states.reported_state END,
                fault_codes = CASE
                    WHEN EXCLUDED.state_version >= device_states.state_version
                    THEN EXCLUDED.fault_codes ELSE device_states.fault_codes END,
                state_version = GREATEST(device_states.state_version, EXCLUDED.state_version),
                last_seen_at = GREATEST(device_states.last_seen_at, EXCLUDED.last_seen_at),
                reported_at = CASE
                    WHEN EXCLUDED.state_version >= device_states.state_version
                    THEN EXCLUDED.reported_at ELSE device_states.reported_at END
            """
        ),
        {
            "device_id": payload.device_id,
            "state": state_json,
            "fault_codes": faults_json,
            "state_version": payload.state_version,
            "retained": retained,
            "occurred_at": occurred_at,
        },
    )
    await session.execute(
        text(
            """
            INSERT INTO device_state_history (
                id, device_id, message_id, operational_status, reported_state,
                fault_codes, state_version, occurred_at
            )
            SELECT :id, :device_id, :message_id, 'online',
                   CAST(:state AS jsonb), :fault_codes, :state_version, :occurred_at
            WHERE NOT EXISTS (
                SELECT 1 FROM device_state_history
                WHERE device_id = :device_id AND state_version = :state_version
            )
            """
        ),
        {
            "id": uuid4(),
            "device_id": payload.device_id,
            "message_id": message_id,
            "state": state_json,
            "fault_codes": faults_json,
            "state_version": payload.state_version,
            "occurred_at": occurred_at,
        },
    )
    await session.commit()


async def persist_gateway_availability(
    session: AsyncSession,
    *,
    site_id: UUID,
    classroom_id: UUID,
    occurred_at: datetime,
    payload: AvailabilityPayload,
) -> None:
    result = await session.execute(
        text(
            """
            UPDATE device_states AS state
            SET operational_status = :status,
                last_seen_at = GREATEST(state.last_seen_at, :occurred_at)
            FROM devices AS device
            JOIN classrooms AS classroom ON classroom.id = device.classroom_id
            WHERE state.device_id = device.id
              AND device.classroom_id = :classroom_id
              AND classroom.site_id = :site_id
              AND device.hardware_uid LIKE 'aceo-local-relay-%'
            RETURNING state.device_id
            """
        ),
        {
            "status": "online" if payload.status.value == "online" else "offline",
            "occurred_at": occurred_at,
            "site_id": site_id,
            "classroom_id": classroom_id,
        },
    )
    if not result.fetchall():
        raise ValueError("gateway availability does not identify a registered relay controller")
    await session.commit()


async def persist_command_acknowledgement(
    session: AsyncSession,
    *,
    site_id: UUID,
    classroom_id: UUID,
    message_id: UUID,
    occurred_at: datetime,
    payload: CommandAcknowledgementPayload,
) -> None:
    await _assert_device_identity(session, payload.device_id, site_id, classroom_id)
    result = await session.execute(
        text(
            """
            SELECT id, classroom_id, device_id
            FROM device_commands
            WHERE id = :command_id
            """
        ),
        {"command_id": payload.command_id},
    )
    command: Any = result.mappings().one_or_none()
    if (
        command is None
        or command.device_id != payload.device_id
        or command.classroom_id != classroom_id
    ):
        raise ValueError("acknowledgement does not match a persisted command")

    status_map = {
        AcknowledgementStatus.ACCEPTED: "accepted",
        AcknowledgementStatus.APPLIED: "applied",
        AcknowledgementStatus.REJECTED: "rejected",
        AcknowledgementStatus.FAILED: "failed",
    }
    command_status = status_map[payload.status]
    state_json = json.dumps(payload.reported_state) if payload.reported_state is not None else None
    await session.execute(
        text(
            """
            INSERT INTO command_events (
                id, command_id, message_id, status, reason_code, details,
                reported_state, occurred_at
            )
            VALUES (
                :id, :command_id, :message_id, :status, :reason_code,
                '{}'::jsonb, CAST(:reported_state AS jsonb), :occurred_at
            )
            ON CONFLICT (message_id) DO NOTHING
            """
        ),
        {
            "id": uuid4(),
            "command_id": payload.command_id,
            "message_id": message_id,
            "status": command_status,
            "reason_code": payload.reason_code,
            "reported_state": state_json,
            "occurred_at": occurred_at,
        },
    )
    await session.execute(
        text(
            """
            UPDATE device_commands
            SET status = :status,
                acknowledged_at = COALESCE(acknowledged_at, :occurred_at),
                applied_at = COALESCE(applied_at, :applied_at)
            WHERE id = :command_id
              AND status IN ('queued', 'published', 'accepted')
            """
        ),
        {
            "command_id": payload.command_id,
            "status": command_status,
            "occurred_at": occurred_at,
            "applied_at": payload.applied_at,
        },
    )
    if payload.reported_state is not None and payload.status is AcknowledgementStatus.APPLIED:
        await session.execute(
            text(
                """
                UPDATE device_states
                SET reported_state = CASE
                        WHEN :state_version >= state_version
                        THEN CAST(:state AS jsonb) ELSE reported_state END,
                    state_version = GREATEST(state_version, :state_version),
                    last_seen_at = GREATEST(last_seen_at, :occurred_at),
                    reported_at = CASE
                        WHEN :state_version >= state_version
                        THEN :occurred_at ELSE reported_at END,
                    operational_status = CASE
                        WHEN last_seen_at IS NULL OR :occurred_at >= last_seen_at
                        THEN 'online' ELSE operational_status END
                WHERE device_id = :device_id
                """
            ),
            {
                "device_id": payload.device_id,
                "state": state_json,
                "state_version": int(payload.reported_state.get("state_version", 0)),
                "occurred_at": occurred_at,
            },
        )
    await session.commit()


async def _assert_device_identity(
    session: AsyncSession,
    device_id: UUID,
    site_id: UUID,
    classroom_id: UUID,
) -> None:
    result = await session.execute(
        text(
            """
            SELECT c.site_id, d.classroom_id
            FROM devices AS d
            JOIN classrooms AS c ON c.id = d.classroom_id
            WHERE d.id = :device_id
            """
        ),
        {"device_id": device_id},
    )
    row: Any = result.mappings().one_or_none()
    if row is None or row.site_id != site_id or row.classroom_id != classroom_id:
        raise ValueError("device identity does not match MQTT topic and envelope")
