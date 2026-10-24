"""Deterministic local relay policy and atomic command/outbox persistence."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.domain.local_hardware import (
    LOCAL_CLASSROOM_ID,
    LOCAL_OPERATOR_ID,
    LOCAL_RELAY_DEVICE_IDS,
    LOCAL_SITE_ID,
)
from app.schemas.common import CommandAction, CommandStatus
from app.schemas.device_control import ManualCommandRequest, ManualCommandResponse

MAX_DEVICE_STATE_AGE = timedelta(seconds=15)
COMMAND_COOLDOWN = timedelta(seconds=2)


@dataclass(frozen=True)
class DeviceCommandSubmission:
    response: ManualCommandResponse
    replayed: bool


async def submit_manual_command(
    sessions: async_sessionmaker[AsyncSession],
    *,
    device_id: UUID,
    idempotency_key: str,
    request: ManualCommandRequest,
) -> DeviceCommandSubmission:
    if device_id not in LOCAL_RELAY_DEVICE_IDS:
        raise DeviceNotFoundError

    now = datetime.now(UTC)
    request_id = uuid4()
    decision_id = uuid4()
    command_id = uuid4()
    requested_value = _bounded_value(request)

    async with sessions() as session, session.begin():
        await session.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:lock_key, 0))"),
            {"lock_key": f"{LOCAL_OPERATOR_ID}:{idempotency_key}"},
        )
        result = await session.execute(
            text(
                """
                SELECT d.classroom_id, d.enabled, d.capabilities,
                       c.control_policy_id,
                       ds.operational_status, ds.reported_state,
                       ds.fault_codes, ds.last_seen_at,
                       env.occurred_at AS environment_at,
                       env.motion_detected, env.sample_interval_ms,
                       pending.id AS pending_command_id,
                       recent.applied_at AS previous_applied_at
                FROM devices AS d
                JOIN classrooms AS c ON c.id = d.classroom_id
                LEFT JOIN device_states AS ds ON ds.device_id = d.id
                LEFT JOIN LATERAL (
                    SELECT occurred_at, motion_detected, sample_interval_ms
                    FROM environment_readings
                    WHERE classroom_id = d.classroom_id
                    ORDER BY occurred_at DESC, message_id DESC
                    LIMIT 1
                ) AS env ON true
                LEFT JOIN LATERAL (
                    SELECT id
                    FROM device_commands
                    WHERE device_id = d.id AND status IN ('queued', 'published', 'accepted')
                    ORDER BY created_at DESC
                    LIMIT 1
                ) AS pending ON true
                LEFT JOIN LATERAL (
                    SELECT applied_at
                    FROM device_commands
                    WHERE device_id = d.id AND status = 'applied' AND applied_at IS NOT NULL
                    ORDER BY applied_at DESC
                    LIMIT 1
                ) AS recent ON true
                WHERE d.id = :device_id
                  AND d.classroom_id = :classroom_id
                  AND c.site_id = :site_id
                  AND d.hardware_uid LIKE 'aceo-local-relay-%'
                FOR UPDATE OF d
                """
            ),
            {
                "device_id": device_id,
                "classroom_id": LOCAL_CLASSROOM_ID,
                "site_id": LOCAL_SITE_ID,
            },
        )
        device: Any = result.mappings().one_or_none()
        if device is None:
            raise DeviceNotFoundError

        replay = await _existing_idempotent_request(
            session,
            idempotency_key=idempotency_key,
            device_id=device_id,
            request=request,
        )
        if replay is not None:
            return DeviceCommandSubmission(replay, replayed=True)

        reasons, summary, matched_rule = _evaluate_policy(
            device=device,
            request=request,
            now=now,
        )
        allowed = not reasons
        expires_at = now + timedelta(seconds=request.expires_in_seconds)
        bounded_action = request.action if allowed else None
        bounded_value = requested_value if allowed else None
        snapshot = {
            "device_id": str(device_id),
            "classroom_id": str(device.classroom_id),
            "operational_status": device.operational_status,
            "reported_state": device.reported_state or {},
            "fault_codes": list(device.fault_codes or []),
            "last_seen_at": _iso(device.last_seen_at),
            "environment_observed_at": _iso(device.environment_at),
            "motion_detected": device.motion_detected,
            "sample_interval_ms": device.sample_interval_ms,
            "action": request.action.value,
            "requested_value": requested_value,
            "expires_at": expires_at.isoformat(),
        }

        await session.execute(
            text(
                """
                INSERT INTO control_requests (
                    id, classroom_id, device_id, trigger, requested_action,
                    requested_value, requested_by, reason, idempotency_key,
                    requested_at, expires_at
                )
                VALUES (
                    :id, :classroom_id, :device_id, 'manual', :action,
                    CAST(:value AS jsonb), :requested_by, :reason,
                    :idempotency_key, :requested_at, :expires_at
                )
                """
            ),
            {
                "id": request_id,
                "classroom_id": device.classroom_id,
                "device_id": device_id,
                "action": request.action.value,
                "value": json.dumps(requested_value),
                "requested_by": LOCAL_OPERATOR_ID,
                "reason": request.reason,
                "idempotency_key": idempotency_key,
                "requested_at": now,
                "expires_at": expires_at,
            },
        )
        await session.execute(
            text(
                """
                INSERT INTO control_decisions (
                    id, request_id, policy_version_id, outcome, reason_codes,
                    summary, input_snapshot, matched_rule, bounded_action, bounded_value,
                    evaluated_at
                )
                VALUES (
                    :id, :request_id, :policy_version_id, :outcome,
                    CAST(:reason_codes AS text[]), :summary,
                    CAST(:input_snapshot AS jsonb), :matched_rule,
                    :bounded_action, CAST(:bounded_value AS jsonb), :evaluated_at
                )
                """
            ),
            {
                "id": decision_id,
                "request_id": request_id,
                "policy_version_id": device.control_policy_id,
                "outcome": "allowed" if allowed else "denied",
                "reason_codes": reasons or ["authorized_manual_request", "device_available"],
                "summary": summary,
                "input_snapshot": json.dumps(snapshot),
                "matched_rule": matched_rule,
                "bounded_action": bounded_action.value if bounded_action else None,
                "bounded_value": json.dumps(bounded_value) if bounded_value else None,
                "evaluated_at": now,
            },
        )

        if allowed:
            assert bounded_action is not None
            await session.execute(
                text(
                    """
                    INSERT INTO device_commands (
                        id, decision_id, classroom_id, device_id, action,
                        value, idempotency_key, status, created_at, expires_at
                    )
                    VALUES (
                        :id, :decision_id, :classroom_id, :device_id, :action,
                        CAST(:value AS jsonb), :idempotency_key, 'queued',
                        :created_at, :expires_at
                    )
                    """
                ),
                {
                    "id": command_id,
                    "decision_id": decision_id,
                    "classroom_id": device.classroom_id,
                    "device_id": device_id,
                    "action": bounded_action.value,
                    "value": json.dumps(bounded_value),
                    "idempotency_key": idempotency_key,
                    "created_at": now,
                    "expires_at": expires_at,
                },
            )

    response = ManualCommandResponse(
        request_id=request_id,
        decision_id=decision_id,
        decision="allowed" if allowed else "denied",
        reason_codes=reasons or ["authorized_manual_request", "device_available"],
        summary=summary,
        command_id=command_id if allowed else None,
        command_status=CommandStatus.QUEUED if allowed else None,
    )
    return DeviceCommandSubmission(response, replayed=False)


def _bounded_value(request: ManualCommandRequest) -> dict[str, Any]:
    if request.action is CommandAction.SET_POWER:
        return {"on": request.value["on"], "mode": "manual"}
    return {"mode": request.value["mode"]}


def _evaluate_policy(
    *, device: Any, request: ManualCommandRequest, now: datetime
) -> tuple[list[str], str, str]:
    reasons: list[str] = []
    if not device.enabled:
        reasons.append("device_disabled")
    if device.operational_status != "online":
        reasons.append("device_offline")
    if device.last_seen_at is None or now - device.last_seen_at > MAX_DEVICE_STATE_AGE:
        reasons.append("device_state_stale")
    elif device.last_seen_at > now + timedelta(seconds=5):
        reasons.append("device_clock_skew")
    if device.fault_codes:
        reasons.append("device_fault")
    capabilities = device.capabilities or {}
    if request.action.value not in capabilities.get("supported_actions", []):
        reasons.append("unsupported_action")
    if (
        request.action is CommandAction.SET_POWER
        and (device.reported_state or {}).get("mode") != "manual"
    ):
        reasons.append("device_auto_mode")
    if device.pending_command_id is not None:
        reasons.append("command_pending")
    if (
        device.previous_applied_at is not None
        and now - device.previous_applied_at < COMMAND_COOLDOWN
    ):
        reasons.append("command_cooldown")

    if request.action is CommandAction.SET_POWER and request.value["on"]:
        if device.environment_at is None:
            reasons.append("safety_state_unknown")
        else:
            sample_interval = max(int(device.sample_interval_ms or 2000), 2000)
            max_environment_age = timedelta(milliseconds=sample_interval * 3)
            if now - device.environment_at > max_environment_age:
                reasons.append("safety_state_stale")
            if device.environment_at > now + timedelta(seconds=5):
                reasons.append("safety_state_clock_skew")
            if device.motion_detected is None:
                reasons.append("motion_state_unknown")
            elif not device.motion_detected:
                reasons.append("pir_master_no_motion")

    if reasons:
        return (
            reasons,
            "The request was denied by the local control policy.",
            "universal_safety_checks",
        )
    return (
        [],
        "The request passed the local manual-control safety checks.",
        "authorized_manual_request",
    )


async def _existing_idempotent_request(
    session: AsyncSession,
    *,
    idempotency_key: str,
    device_id: UUID,
    request: ManualCommandRequest,
) -> ManualCommandResponse | None:
    result = await session.execute(
        text(
            """
            SELECT r.id AS request_id, r.device_id, r.requested_action,
                   r.requested_value, r.reason, d.id AS decision_id,
                   r.requested_at, r.expires_at,
                   d.outcome, d.reason_codes, d.summary, c.id AS command_id,
                   c.status AS command_status
            FROM control_requests AS r
            JOIN control_decisions AS d ON d.request_id = r.id
            LEFT JOIN device_commands AS c ON c.decision_id = d.id
            WHERE r.requested_by = :requested_by AND r.idempotency_key = :key
            """
        ),
        {"requested_by": LOCAL_OPERATOR_ID, "key": idempotency_key},
    )
    row: Any = result.mappings().one_or_none()
    if row is None:
        return None
    desired = _bounded_value(request)
    if (
        row.device_id != device_id
        or row.requested_action != request.action.value
        or row.reason != request.reason
        or _json_object(row.requested_value) != desired
        or int((row.expires_at - row.requested_at).total_seconds()) != request.expires_in_seconds
    ):
        raise IdempotencyConflictError
    return ManualCommandResponse(
        request_id=row.request_id,
        decision_id=row.decision_id,
        decision=row.outcome,
        reason_codes=list(row.reason_codes),
        summary=row.summary,
        command_id=row.command_id,
        command_status=CommandStatus(row.command_status) if row.command_status else None,
    )


def _json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, str):
        value = json.loads(value)
    return dict(value)


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


class DeviceNotFoundError(Exception):
    """No registered device has the supplied identifier."""


class IdempotencyConflictError(Exception):
    """The idempotency key was already used for another request."""
