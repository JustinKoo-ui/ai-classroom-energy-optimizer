"""Read-only device observer persistence, isolated from operational state writes.

Segments compress repeated live MQTT observations. They are evidence windows,
not exact relay-transition timestamps. Gaps are never bridged beyond the TTL.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.local_hardware import LOCAL_RELAY_DEVICE_IDS
from app.schemas.mqtt import DeviceStatePayload


@dataclass(frozen=True)
class RuntimeHistory:
    classroom_exists: bool
    tracking_started_at: datetime | None
    segments: list[dict[str, Any]]
    timezone: str
    tracking_starts_by_device: dict[UUID, datetime] = field(default_factory=dict)


async def store_runtime_observation(
    session: AsyncSession,
    *,
    classroom_id: UUID,
    message_id: UUID,
    occurred_at: datetime,
    payload: DeviceStatePayload,
    retained: bool,
    stale_after_seconds: int = 15,
    now: datetime | None = None,
) -> bool:
    """Store fresh firmware evidence; return false for replay or stale data.

    The caller has already validated the MQTT device identity in the existing
    operational persistence path. This transaction is intentionally separate.
    """
    received_at = now or datetime.now(UTC)
    if not 2 <= stale_after_seconds <= 300:
        raise ValueError("stale_after_seconds must be between 2 and 300")
    if occurred_at.tzinfo is None or received_at.tzinfo is None:
        raise ValueError("runtime observations require timezone-aware timestamps")
    if (
        retained
        or payload.device_id not in LOCAL_RELAY_DEVICE_IDS
        or occurred_at > received_at + timedelta(seconds=5)
        or received_at - occurred_at > timedelta(seconds=stale_after_seconds)
    ):
        return False

    quality = ["firmware_fault"] if payload.fault_codes else []
    on = payload.state.get("on")
    state = "unknown" if quality or not isinstance(on, bool) else "on" if on else "off"
    mode = payload.state.get("mode", "unknown")
    if mode not in {"auto", "manual"}:
        mode = "unknown"
    async with session.begin():
        # Serializes concurrent MQTT callbacks, including the first observation.
        await session.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:lock_key, 0))"),
            {"lock_key": f"analytics-runtime:{payload.device_id}"},
        )
        result = await session.execute(
            text(
                """
                SELECT id, state, mode, quality, last_observed_at, freshness_limit_seconds,
                       first_message_id, last_message_id
                FROM analytics_runtime_segments
                WHERE device_id = :device_id AND classroom_id = :classroom_id
                ORDER BY last_observed_at DESC, id DESC
                LIMIT 1
                FOR UPDATE
                """
            ),
            {"device_id": payload.device_id, "classroom_id": classroom_id},
        )
        previous = result.mappings().one_or_none()
        if previous is not None and (
            occurred_at <= previous["last_observed_at"]
            or message_id in {previous["first_message_id"], previous["last_message_id"]}
        ):
            return False
        can_extend = previous is not None and (
            previous["state"] == state
            and previous["mode"] == mode
            and list(previous["quality"] or []) == quality
            and previous["freshness_limit_seconds"] == stale_after_seconds
            and occurred_at - previous["last_observed_at"]
            <= timedelta(seconds=stale_after_seconds)
        )
        if can_extend and previous is not None:
            await session.execute(
                text(
                    """
                    UPDATE analytics_runtime_segments
                    SET last_observed_at = :occurred_at, last_message_id = :message_id
                    WHERE id = :id
                    """
                ),
                {"id": previous["id"], "occurred_at": occurred_at, "message_id": message_id},
            )
        else:
            await session.execute(
                text(
                    """
                    INSERT INTO analytics_runtime_segments (
                        id, classroom_id, device_id, state, mode,
                        first_observed_at, last_observed_at,
                        first_message_id, last_message_id, quality, freshness_limit_seconds
                    )
                    SELECT :id, :classroom_id, :device_id, :state, :mode,
                           :occurred_at, :occurred_at, :message_id, :message_id, :quality,
                           :freshness_limit_seconds
                    FROM devices
                    WHERE id = :device_id AND classroom_id = :classroom_id
                    """
                ),
                {
                    "id": uuid4(),
                    "classroom_id": classroom_id,
                    "device_id": payload.device_id,
                    "state": state,
                    "mode": mode,
                    "occurred_at": occurred_at,
                    "message_id": message_id,
                    "quality": quality,
                    "freshness_limit_seconds": stale_after_seconds,
                },
            )
    return True


async def load_runtime_history(
    session: AsyncSession,
    classroom_id: UUID,
    start: datetime,
    end: datetime,
    stale_after_seconds: int = 15,
) -> RuntimeHistory:
    """Load evidence overlapping a report, retaining the preceding TTL tail."""
    if start.tzinfo is None or end.tzinfo is None or end <= start:
        raise ValueError("runtime reporting requires an increasing timezone-aware period")
    if not 2 <= stale_after_seconds <= 300:
        raise ValueError("stale_after_seconds must be between 2 and 300")
    parameters = {
        "classroom_id": classroom_id,
        "device_ids": list(LOCAL_RELAY_DEVICE_IDS),
        # A previous segment retains the TTL used when it was observed, even
        # after configuration changes. Include the maximum valid prior tail.
        "start": start - timedelta(seconds=300),
        "end": end,
    }
    result = await session.execute(
        text(
            """
            SELECT classroom.timezone,
                   MIN(segment.first_observed_at) AS tracking_started_at
            FROM classrooms AS classroom
            LEFT JOIN analytics_runtime_segments AS segment
              ON segment.classroom_id = classroom.id
             AND segment.device_id = ANY(CAST(:device_ids AS uuid[]))
            WHERE classroom.id = :classroom_id
            GROUP BY classroom.id, classroom.timezone
            """
        ),
        parameters,
    )
    classroom = result.mappings().one_or_none()
    if classroom is None:
        return RuntimeHistory(False, None, [], "Asia/Kuala_Lumpur")
    result = await session.execute(
        text(
            """
            SELECT device_id, MIN(first_observed_at) AS tracking_started_at
            FROM analytics_runtime_segments
            WHERE classroom_id = :classroom_id
              AND device_id = ANY(CAST(:device_ids AS uuid[]))
            GROUP BY device_id
            """
        ),
        parameters,
    )
    tracking_starts = {
        row["device_id"]: row["tracking_started_at"] for row in result.mappings().all()
    }
    result = await session.execute(
        text(
            """
            SELECT id, classroom_id, device_id, state, mode, source, timestamp_kind,
                   first_observed_at, last_observed_at, quality, freshness_limit_seconds
            FROM analytics_runtime_segments
            WHERE classroom_id = :classroom_id
              AND device_id = ANY(CAST(:device_ids AS uuid[]))
              AND first_observed_at < :end
              AND last_observed_at >= :start
            ORDER BY device_id, first_observed_at, id
            """
        ),
        parameters,
    )
    return RuntimeHistory(
        True,
        classroom["tracking_started_at"],
        [dict(row) for row in result.mappings().all()],
        classroom["timezone"],
        tracking_starts,
    )
