"""Persistence and command-outbox orchestration for occupancy automation."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.domain.local_hardware import (
    AC_DEVICE_ID,
    FAN_DEVICE_ID,
    LIGHT_DEVICE_ID,
    LOCAL_CLASSROOM_ID,
    LOCAL_OPERATOR_ID,
)
from app.services.occupancy_automation_policy import (
    DEVICE_NAMES,
    OCCUPANCY_THRESHOLD,
    AutomationInputs,
    AutomationState,
    DevicePolicyState,
    evaluate_automation,
)

OCCUPANCY_STALE_AFTER = timedelta(seconds=15)
DEVICE_STATE_STALE_AFTER = timedelta(seconds=15)
COMMAND_COOLDOWN = timedelta(seconds=2)
COMMAND_LIFETIME = timedelta(seconds=30)

DEVICE_IDS = {
    "fan": FAN_DEVICE_ID,
    "lighting": LIGHT_DEVICE_ID,
    "air_conditioner": AC_DEVICE_ID,
}


async def evaluate_local_automation(
    sessions: async_sessionmaker[AsyncSession],
    *,
    now: datetime | None = None,
) -> int:
    """Evaluate one classroom snapshot and persist only policy-approved commands."""
    current_time = now or datetime.now(UTC)
    queued = 0
    async with sessions() as session, session.begin():
        await session.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
            {"key": f"occupancy-automation:{LOCAL_CLASSROOM_ID}"},
        )
        await session.execute(
            text(
                """
                INSERT INTO occupancy_automation_state (classroom_id)
                VALUES (:classroom_id)
                ON CONFLICT (classroom_id) DO NOTHING
                """
            ),
            {"classroom_id": LOCAL_CLASSROOM_ID},
        )
        state_result = await session.execute(
            text(
                """
                SELECT qualifying_since, below_threshold_since, pir_scan_until,
                       fallback_lights_latched, last_motion_detected,
                       camera_was_fresh, urgency_started_at, urgency_until,
                       urgency_result, urgency_restore_states, updated_at
                FROM occupancy_automation_state
                WHERE classroom_id = :classroom_id
                FOR UPDATE
                """
            ),
            {"classroom_id": LOCAL_CLASSROOM_ID},
        )
        state_row = state_result.mappings().one()
        policy_result = await session.execute(
            text("SELECT control_policy_id FROM classrooms WHERE id = :classroom_id"),
            {"classroom_id": LOCAL_CLASSROOM_ID},
        )
        policy_id = policy_result.scalar_one_or_none()
        if policy_id is None:
            return 0

        occupancy_result = await session.execute(
            text(
                """
                SELECT occupancy.person_count, occupancy.observed_at,
                       EXISTS (
                           SELECT 1 FROM (
                               SELECT person_count, occurred_at,
                                      lag(occurred_at) OVER (ORDER BY occurred_at) AS previous_at
                               FROM occupancy_observations
                               WHERE classroom_id = occupancy.classroom_id
                                 AND occurred_at >= CAST(:qualifying_since AS timestamptz)
                                     - CAST(:occupancy_stale_after AS interval)
                                 AND occurred_at <= occupancy.observed_at
                           ) AS observation
                           WHERE observation.occurred_at >= :qualifying_since
                             AND (
                                 observation.person_count < :qualifying_count
                                 OR observation.occurred_at - observation.previous_at
                                     > CAST(:occupancy_stale_after AS interval)
                             )
                       ) AS qualification_interrupted
                FROM occupancy_current AS occupancy
                WHERE occupancy.classroom_id = :classroom_id
                """
            ),
            {
                "classroom_id": LOCAL_CLASSROOM_ID,
                "qualifying_count": OCCUPANCY_THRESHOLD,
                "qualifying_since": state_row["qualifying_since"],
                "occupancy_stale_after": OCCUPANCY_STALE_AFTER,
            },
        )
        occupancy_row = occupancy_result.mappings().one_or_none()
        occupancy_at = occupancy_row["observed_at"] if occupancy_row else None
        occupancy_age = current_time - occupancy_at if occupancy_at else None
        occupancy_fresh = (
            occupancy_age is not None
            and timedelta(0) <= occupancy_age <= OCCUPANCY_STALE_AFTER
        )
        occupancy_count = (
            occupancy_row["person_count"]
            if occupancy_row is not None and occupancy_fresh
            else None
        )

        environment_result = await session.execute(
            text(
                """
                SELECT temperature_c, relative_humidity_pct, illuminance_adc,
                       motion_detected, sample_interval_ms, occurred_at
                FROM environment_readings
                WHERE classroom_id = :classroom_id
                ORDER BY occurred_at DESC, message_id DESC
                LIMIT 1
                """
            ),
            {"classroom_id": LOCAL_CLASSROOM_ID},
        )
        environment_row = environment_result.mappings().one_or_none()
        environment_at = environment_row["occurred_at"] if environment_row else None
        max_environment_age = timedelta(
            milliseconds=max(
                int(environment_row["sample_interval_ms"] or 2000)
                if environment_row
                else 2000,
                2000,
            )
            * 3
        )
        environment_age = current_time - environment_at if environment_at else None
        environment_fresh = (
            environment_age is not None
            and timedelta(0) <= environment_age <= max_environment_age
        )

        device_result = await session.execute(
            text(
                """
                SELECT d.id, d.enabled, d.capabilities,
                       ds.operational_status, ds.reported_state, ds.fault_codes,
                       ds.last_seen_at,
                       pending.id AS pending_command_id,
                       recent.applied_at AS previous_applied_at
                FROM devices AS d
                LEFT JOIN device_states AS ds ON ds.device_id = d.id
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
                WHERE d.classroom_id = :classroom_id
                  AND d.id = ANY(CAST(:device_ids AS uuid[]))
                FOR UPDATE OF d
                """
            ),
            {
                "classroom_id": LOCAL_CLASSROOM_ID,
                "device_ids": list(DEVICE_IDS.values()),
            },
        )
        device_rows = {
            next(name for name, device_id in DEVICE_IDS.items() if row["id"] == device_id): row
            for row in device_result.mappings().all()
        }
        device_states: dict[str, DevicePolicyState] = {}
        for name in DEVICE_NAMES:
            row = device_rows.get(name)
            reported = _json_object(row["reported_state"]) if row else {}
            device_states[name] = DevicePolicyState(
                on=reported.get("on") is True,
                mode=reported.get("mode")
                if reported.get("mode") in {"auto", "manual"}
                else "auto",
            )

        restore_states = _restore_states(state_row["urgency_restore_states"])
        is_urgency_active = (
            state_row["urgency_until"] is not None
            and state_row["urgency_until"] > current_time
        )
        evaluation_age = current_time - state_row["updated_at"]
        policy_state = AutomationState(
            # An unobserved backend outage cannot establish continuous occupancy.
            qualifying_since=(
                state_row["qualifying_since"]
                if (
                    timedelta(0) <= evaluation_age <= OCCUPANCY_STALE_AFTER
                    and occupancy_row is not None
                    and not occupancy_row["qualification_interrupted"]
                )
                else None
            ),
            below_threshold_since=state_row["below_threshold_since"],
            pir_scan_until=state_row["pir_scan_until"],
            fallback_lights_latched=state_row["fallback_lights_latched"],
            last_motion_detected=state_row["last_motion_detected"],
            camera_was_fresh=state_row["camera_was_fresh"],
            urgency_started_at=(
                state_row["urgency_started_at"]
                if state_row["urgency_until"] is not None
                else None
            ),
            urgency_until=state_row["urgency_until"],
            urgency_restore_states=restore_states,
        )
        decision = evaluate_automation(
            policy_state,
            AutomationInputs(
                now=current_time,
                occupancy_count=occupancy_count,
                occupancy_fresh=occupancy_fresh,
                temperature_c=(
                    float(environment_row["temperature_c"])
                    if environment_row is not None and environment_fresh
                    else None
                ),
                humidity_pct=(
                    float(environment_row["relative_humidity_pct"])
                    if environment_row is not None and environment_fresh
                    else None
                ),
                environment_fresh=environment_fresh,
                ldr_adc=(
                    environment_row["illuminance_adc"]
                    if environment_row is not None and environment_fresh
                    else None
                ),
                pir_motion=(
                    environment_row["motion_detected"]
                    if environment_row is not None and environment_fresh
                    else None
                ),
                pir_fresh=(
                    environment_fresh
                    and environment_row is not None
                    and environment_row["motion_detected"] is not None
                ),
                devices=device_states,
            ),
        )

        stored_started_at = decision.state.urgency_started_at
        urgency_result = state_row["urgency_result"]
        if decision.urgency_status.startswith("completed_"):
            stored_started_at = None
            urgency_result = decision.urgency_status
        elif decision.urgency_status == "monitoring":
            urgency_result = "monitoring"
        await session.execute(
            text(
                """
                UPDATE occupancy_automation_state
                SET qualifying_since = :qualifying_since,
                    below_threshold_since = :below_threshold_since,
                    pir_scan_until = :pir_scan_until,
                    fallback_lights_latched = :fallback_lights_latched,
                    last_motion_detected = :last_motion_detected,
                    camera_was_fresh = :camera_was_fresh,
                    urgency_started_at = :urgency_started_at,
                    urgency_until = :urgency_until,
                    urgency_result = :urgency_result,
                    urgency_restore_states = CAST(:urgency_restore_states AS jsonb),
                    updated_at = :now
                WHERE classroom_id = :classroom_id
                """
            ),
            {
                "classroom_id": LOCAL_CLASSROOM_ID,
                "qualifying_since": decision.state.qualifying_since,
                "below_threshold_since": decision.state.below_threshold_since,
                "pir_scan_until": decision.state.pir_scan_until,
                "fallback_lights_latched": decision.state.fallback_lights_latched,
                "last_motion_detected": decision.state.last_motion_detected,
                "camera_was_fresh": decision.state.camera_was_fresh,
                "urgency_started_at": stored_started_at,
                "urgency_until": decision.state.urgency_until,
                "urgency_result": urgency_result,
                "urgency_restore_states": json.dumps(
                    {
                        name: {"on": value.on, "mode": value.mode}
                        for name, value in (decision.state.urgency_restore_states or {}).items()
                    }
                ),
                "now": current_time,
            },
        )

        snapshot = {
            "occupancy_count": occupancy_count,
            "occupancy_fresh": occupancy_fresh,
            "occupancy_observed_at": _iso(occupancy_at),
            "temperature_c": (
                float(environment_row["temperature_c"])
                if environment_row is not None
                else None
            ),
            "relative_humidity_pct": (
                float(environment_row["relative_humidity_pct"])
                if environment_row is not None
                else None
            ),
            "illuminance_adc": (
                environment_row["illuminance_adc"] if environment_row is not None else None
            ),
            "motion_detected": (
                environment_row["motion_detected"] if environment_row is not None else None
            ),
            "environment_observed_at": _iso(environment_at),
            "environment_fresh": environment_fresh,
            "urgency_active": is_urgency_active,
            "evaluated_at": current_time.isoformat(),
        }
        for name in DEVICE_NAMES:
            target = decision.targets[name]
            if target is None:
                continue
            row = device_rows.get(name)
            if row is None:
                continue
            source = decision.sources[name]
            current_mode = device_states[name].mode
            privileged_source = source in {
                "urgency",
                "urgency_timeout",
                "manual_restore",
                "urgency_restore",
            }
            if current_mode == "manual" and not privileged_source:
                continue
            if device_states[name].on == target:
                continue
            if not _device_can_apply(row, current_time):
                continue
            trigger = _trigger_for_source(source)
            value = {
                "on": target,
                "mode": current_mode,
                "source": source,
            }
            await _persist_allowed_command(
                session,
                classroom_id=LOCAL_CLASSROOM_ID,
                device_id=DEVICE_IDS[name],
                policy_id=policy_id,
                trigger=trigger,
                requested_value=value,
                requested_by=None,
                reason=_reason_for_source(source),
                idempotency_key=f"automation:{DEVICE_IDS[name]}:{uuid4()}",
                expires_at=current_time + COMMAND_LIFETIME,
                input_snapshot={**snapshot, "device_mode": current_mode},
                matched_rule=source,
                now=current_time,
            )
            queued += 1

    return queued


async def start_local_urgency(
    sessions: async_sessionmaker[AsyncSession],
    *,
    now: datetime | None = None,
) -> dict[str, str | None]:
    current_time = now or datetime.now(UTC)
    async with sessions() as session, session.begin():
        await session.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
            {"key": f"occupancy-automation:{LOCAL_CLASSROOM_ID}"},
        )
        await session.execute(
            text(
                """
                INSERT INTO occupancy_automation_state (classroom_id)
                VALUES (:classroom_id)
                ON CONFLICT (classroom_id) DO NOTHING
                """
            ),
            {"classroom_id": LOCAL_CLASSROOM_ID},
        )
        state_result = await session.execute(
            text(
                """
                SELECT urgency_started_at, urgency_until, urgency_result
                FROM occupancy_automation_state
                WHERE classroom_id = :classroom_id
                FOR UPDATE
                """
            ),
            {"classroom_id": LOCAL_CLASSROOM_ID},
        )
        urgency_state = state_result.mappings().one()
        if urgency_state["urgency_until"] and urgency_state["urgency_until"] > current_time:
            return {
                "status": "monitoring",
                "started_at": _iso(urgency_state["urgency_started_at"]),
                "expires_at": _iso(urgency_state["urgency_until"]),
            }

        policy_result = await session.execute(
            text("SELECT control_policy_id FROM classrooms WHERE id = :classroom_id"),
            {"classroom_id": LOCAL_CLASSROOM_ID},
        )
        policy_id = policy_result.scalar_one_or_none()
        if policy_id is None:
            raise UrgencyUnavailableError("The local classroom policy is unavailable.")

        device_result = await session.execute(
            text(
                """
                SELECT d.id, d.enabled, d.capabilities,
                       ds.operational_status, ds.reported_state, ds.fault_codes,
                       ds.last_seen_at,
                       pending.id AS pending_command_id,
                       recent.applied_at AS previous_applied_at
                FROM devices AS d
                LEFT JOIN device_states AS ds ON ds.device_id = d.id
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
                WHERE d.classroom_id = :classroom_id
                  AND d.id = ANY(CAST(:device_ids AS uuid[]))
                FOR UPDATE OF d
                """
            ),
            {
                "classroom_id": LOCAL_CLASSROOM_ID,
                "device_ids": list(DEVICE_IDS.values()),
            },
        )
        device_rows = {
            next(name for name, device_id in DEVICE_IDS.items() if row["id"] == device_id): row
            for row in device_result.mappings().all()
        }
        if any(
            name not in device_rows or not _device_can_apply(device_rows[name], current_time)
            for name in DEVICE_NAMES
        ):
            raise UrgencyUnavailableError(
                "Urgency requires all three ESP32 relays to be online, fresh, and fault-free."
            )

        restore_states = {
            name: _reported_policy_state(device_rows[name]["reported_state"])
            for name in DEVICE_NAMES
        }
        started_at = current_time
        expires_at = current_time + timedelta(minutes=5)
        await session.execute(
            text(
                """
                UPDATE occupancy_automation_state
                SET urgency_started_at = :started_at,
                    urgency_until = :expires_at,
                    urgency_result = 'monitoring',
                    urgency_restore_states = CAST(:restore_states AS jsonb),
                    updated_at = :now
                WHERE classroom_id = :classroom_id
                """
            ),
            {
                "classroom_id": LOCAL_CLASSROOM_ID,
                "started_at": started_at,
                "expires_at": expires_at,
                "restore_states": json.dumps(
                    {
                        name: {"on": state.on, "mode": state.mode}
                        for name, state in restore_states.items()
                    }
                ),
                "now": current_time,
            },
        )
        snapshot = {
            "trigger": "urgency",
            "started_at": started_at.isoformat(),
            "expires_at": expires_at.isoformat(),
            "occupancy_count": None,
            "occupancy_observation": "evaluated by policy while active",
            "device_states": {
                name: {"on": state.on, "mode": state.mode}
                for name, state in restore_states.items()
            },
        }
        for name in DEVICE_NAMES:
            if restore_states[name].on:
                continue
            await _persist_allowed_command(
                session,
                classroom_id=LOCAL_CLASSROOM_ID,
                device_id=DEVICE_IDS[name],
                policy_id=policy_id,
                trigger="override",
                requested_value={
                    "on": True,
                    "mode": restore_states[name].mode,
                    "source": "urgency",
                },
                requested_by=LOCAL_OPERATOR_ID,
                reason="User activated five-minute urgency monitoring.",
                idempotency_key=f"urgency:{DEVICE_IDS[name]}:{uuid4()}",
                expires_at=current_time + COMMAND_LIFETIME,
                input_snapshot=snapshot,
                matched_rule="urgency_priority",
                now=current_time,
            )
    return {
        "status": "monitoring",
        "started_at": _iso(started_at),
        "expires_at": _iso(expires_at),
    }


async def get_local_urgency_status(
    sessions: async_sessionmaker[AsyncSession],
    *,
    now: datetime | None = None,
) -> dict[str, str | None]:
    current_time = now or datetime.now(UTC)
    async with sessions() as session:
        result = await session.execute(
            text(
                """
                SELECT urgency_started_at, urgency_until, urgency_result
                FROM occupancy_automation_state
                WHERE classroom_id = :classroom_id
                """
            ),
            {"classroom_id": LOCAL_CLASSROOM_ID},
        )
        row = result.mappings().one_or_none()
    if row is None:
        return {"status": "inactive", "started_at": None, "expires_at": None}
    active = row["urgency_until"] is not None and row["urgency_until"] > current_time
    status = "monitoring" if active else row["urgency_result"]
    return {
        "status": status,
        "started_at": _iso(row["urgency_started_at"]),
        "expires_at": _iso(row["urgency_until"]),
    }


async def _persist_allowed_command(
    session: AsyncSession,
    *,
    classroom_id: UUID,
    device_id: UUID,
    policy_id: UUID,
    trigger: str,
    requested_value: dict[str, Any],
    requested_by: UUID | None,
    reason: str,
    idempotency_key: str,
    expires_at: datetime,
    input_snapshot: dict[str, Any],
    matched_rule: str,
    now: datetime,
) -> None:
    request_id = uuid4()
    decision_id = uuid4()
    command_id = uuid4()
    await session.execute(
        text(
            """
            INSERT INTO control_requests (
                id, classroom_id, device_id, trigger, requested_action,
                requested_value, requested_by, reason, idempotency_key,
                requested_at, expires_at
            )
            VALUES (
                :id, :classroom_id, :device_id, :trigger, 'set_power',
                CAST(:value AS jsonb), :requested_by, :reason, :idempotency_key,
                :now, :expires_at
            )
            """
        ),
        {
            "id": request_id,
            "classroom_id": classroom_id,
            "device_id": device_id,
            "trigger": trigger,
            "value": json.dumps(requested_value),
            "requested_by": requested_by,
            "reason": reason,
            "idempotency_key": idempotency_key,
            "now": now,
            "expires_at": expires_at,
        },
    )
    await session.execute(
        text(
            """
            INSERT INTO control_decisions (
                id, request_id, policy_version_id, outcome, reason_codes,
                summary, input_snapshot, matched_rule, bounded_action,
                bounded_value, evaluated_at
            )
            VALUES (
                :id, :request_id, :policy_id, 'allowed',
                ARRAY['policy_allow', CAST(:matched_rule AS text)], :summary,
                CAST(:snapshot AS jsonb), :matched_rule, 'set_power',
                CAST(:value AS jsonb), :now
            )
            """
        ),
        {
            "id": decision_id,
            "request_id": request_id,
            "policy_id": policy_id,
            "matched_rule": matched_rule,
            "summary": reason,
            "snapshot": json.dumps(input_snapshot),
            "value": json.dumps(requested_value),
            "now": now,
        },
    )
    await session.execute(
        text(
            """
            INSERT INTO device_commands (
                id, decision_id, classroom_id, device_id, action, value,
                idempotency_key, status, created_at, expires_at
            )
            VALUES (
                :id, :decision_id, :classroom_id, :device_id, 'set_power',
                CAST(:value AS jsonb), :idempotency_key, 'queued', :now, :expires_at
            )
            """
        ),
        {
            "id": command_id,
            "decision_id": decision_id,
            "classroom_id": classroom_id,
            "device_id": device_id,
            "value": json.dumps(requested_value),
            "idempotency_key": idempotency_key,
            "now": now,
            "expires_at": expires_at,
        },
    )
    await session.execute(
        text(
            """
            UPDATE device_states
            SET desired_state = CAST(:value AS jsonb)
            WHERE device_id = :device_id
            """
        ),
        {"device_id": device_id, "value": json.dumps(requested_value)},
    )


def _device_can_apply(row: Any, now: datetime) -> bool:
    last_seen = row["last_seen_at"]
    freshness = (
        last_seen is not None
        and timedelta(0) <= now - last_seen <= DEVICE_STATE_STALE_AFTER
    )
    capabilities = _json_object(row["capabilities"])
    reported_state = _json_object(row["reported_state"])
    return (
        row["enabled"] is True
        and row["operational_status"] == "online"
        and freshness
        and not row["fault_codes"]
        and isinstance(reported_state.get("on"), bool)
        and reported_state.get("mode") in {"auto", "manual"}
        and "set_power" in capabilities.get("supported_actions", [])
        and row["pending_command_id"] is None
        and (
            row["previous_applied_at"] is None
            or now - row["previous_applied_at"] >= COMMAND_COOLDOWN
        )
    )


def _trigger_for_source(source: str) -> str:
    if source == "vacancy":
        return "vacancy"
    if source in {"urgency", "urgency_timeout", "manual_restore", "urgency_restore"}:
        return "override"
    if source == "pir_fallback":
        return "lighting"
    return "occupancy"


def _reason_for_source(source: str) -> str:
    return {
        "automation": "Occupancy policy evaluated current fresh sensor state.",
        "vacancy": "A fresh camera reading reports no occupants.",
        "pir_fallback": "PIR-triggered camera scanning requested a temporary light state.",
        "urgency_timeout": "Urgency monitoring ended without a fresh camera-detected person.",
        "manual_restore": "Urgency monitoring ended; restoring the prior manual state.",
        "urgency_restore": (
            "Urgency monitoring ended; restoring the prior output before policy resumes."
        ),
    }.get(source, "Occupancy policy evaluated current fresh sensor state.")


def _reported_policy_state(value: Any) -> DevicePolicyState:
    reported = _json_object(value)
    mode = reported.get("mode")
    if mode not in {"auto", "manual"} or not isinstance(reported.get("on"), bool):
        raise UrgencyUnavailableError("ESP32 has not reported a valid relay mode and state.")
    return DevicePolicyState(on=reported["on"], mode=mode)


def _restore_states(value: Any) -> dict[str, DevicePolicyState] | None:
    restore = _json_object(value)
    if not restore:
        return None
    parsed: dict[str, DevicePolicyState] = {}
    for name, state in restore.items():
        if (
            name in DEVICE_NAMES
            and isinstance(state, dict)
            and isinstance(state.get("on"), bool)
            and state.get("mode") in {"auto", "manual"}
        ):
            parsed[name] = DevicePolicyState(on=state["on"], mode=state["mode"])
    return parsed or None


def _json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, str):
        value = json.loads(value)
    return dict(value) if isinstance(value, dict) else {}


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


class UrgencyUnavailableError(Exception):
    """Urgency could not be safely activated or the local controller is unavailable."""
