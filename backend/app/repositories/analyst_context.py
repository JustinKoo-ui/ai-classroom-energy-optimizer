"""Bounded, read-only sample summaries for optional external commentary.

Only aggregate counts, sensor statistics and machine event codes leave this
adapter. Images, producer identifiers, free-form messages and control request
reasons are deliberately absent.
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

MAX_WINDOW = timedelta(days=32)  # Includes a 31-day calendar month crossing DST.
MAX_HOURLY_BUCKETS = 768
OCCUPANCY_CONFIDENCE_FLOOR = 0.5

ENVIRONMENT_SQL = """
WITH samples AS (
    SELECT *,
           received_at BETWEEN occurred_at - interval '5 seconds'
                           AND occurred_at + interval '30 seconds' AS timely,
           temperature_c BETWEEN -20 AND 80
             AND relative_humidity_pct BETWEEN 0 AND 100 AS valid_values
    FROM environment_readings
    WHERE classroom_id = :classroom_id
      AND occurred_at >= :period_start AND occurred_at < :period_end
), valid AS (
    SELECT * FROM samples WHERE timely AND valid_values
), hourly AS (
    SELECT date_trunc('hour', occurred_at AT TIME ZONE 'UTC') AT TIME ZONE 'UTC'
               AS hour_start,
           COUNT(*) AS sample_count,
           AVG(temperature_c) AS temperature_c_average,
           MIN(temperature_c) AS temperature_c_minimum,
           MAX(temperature_c) AS temperature_c_maximum,
           AVG(relative_humidity_pct) AS relative_humidity_pct_average
    FROM valid GROUP BY hour_start ORDER BY hour_start LIMIT :max_hourly_buckets
)
SELECT
    (SELECT COUNT(*) FROM samples) AS sample_count,
    (SELECT COUNT(*) FROM samples WHERE NOT timely) AS excluded_delayed_count,
    (SELECT COUNT(*) FROM samples WHERE NOT valid_values OR valid_values IS NULL)
        AS excluded_invalid_count,
    COUNT(*) AS valid_sample_count,
    MIN(occurred_at) AS first_observed_at,
    MAX(occurred_at) AS last_observed_at,
    AVG(temperature_c) AS temperature_average,
    MIN(temperature_c) AS temperature_minimum,
    MAX(temperature_c) AS temperature_maximum,
    (SELECT temperature_c FROM valid ORDER BY occurred_at, message_id LIMIT 1)
        AS temperature_first,
    (SELECT temperature_c FROM valid ORDER BY occurred_at DESC, message_id DESC LIMIT 1)
        AS temperature_last,
    AVG(relative_humidity_pct) AS humidity_average,
    MIN(relative_humidity_pct) AS humidity_minimum,
    MAX(relative_humidity_pct) AS humidity_maximum,
    (SELECT relative_humidity_pct FROM valid ORDER BY occurred_at, message_id LIMIT 1)
        AS humidity_first,
    (SELECT relative_humidity_pct FROM valid ORDER BY occurred_at DESC, message_id DESC LIMIT 1)
        AS humidity_last,
    COALESCE((SELECT jsonb_agg(to_jsonb(hourly) ORDER BY hour_start) FROM hourly), '[]'::jsonb)
        AS hourly
FROM valid
"""

OCCUPANCY_SQL = """
WITH samples AS (
    SELECT *,
           received_at BETWEEN occurred_at - interval '5 seconds'
                           AND occurred_at + interval '15 seconds' AS timely
    FROM occupancy_observations
    WHERE classroom_id = :classroom_id
      AND occurred_at >= :period_start AND occurred_at < :period_end
), valid AS (
    SELECT * FROM samples
    WHERE timely AND confidence >= :confidence_floor
      AND source = 'roboflow_bytetrack' AND person_count >= 0
), hourly AS (
    SELECT date_trunc('hour', occurred_at AT TIME ZONE 'UTC') AT TIME ZONE 'UTC'
               AS hour_start,
           COUNT(*) AS sample_count,
           AVG(person_count) AS person_count_average,
           MIN(person_count) AS person_count_minimum,
           MAX(person_count) AS person_count_maximum,
           COUNT(*) FILTER (WHERE person_count > 0) AS positive_count_samples,
           COUNT(*) FILTER (WHERE person_count = 0) AS zero_count_samples
    FROM valid GROUP BY hour_start ORDER BY hour_start LIMIT :max_hourly_buckets
), event_counts AS (
    SELECT code, severity::text AS severity, COUNT(*) AS count
    FROM system_events
    WHERE classroom_id = :classroom_id
      AND occurred_at >= :period_start AND occurred_at < :period_end
    GROUP BY code, severity ORDER BY count DESC, code, severity LIMIT 30
), requests AS (
    SELECT
        COUNT(*) FILTER (WHERE trigger = 'manual') AS manual_request_count,
        COUNT(*) FILTER (WHERE trigger = 'override') AS override_request_count,
        COUNT(*) FILTER (WHERE requested_value->>'source' IN
            ('urgency', 'urgency_timeout', 'urgency_restore', 'manual_restore'))
            AS urgency_request_count,
        COUNT(*) FILTER (WHERE trigger IN ('occupancy', 'comfort', 'lighting', 'vacancy'))
            AS automation_request_count
    FROM control_requests
    WHERE classroom_id = :classroom_id
      AND requested_at >= :period_start AND requested_at < :period_end
)
SELECT
    (SELECT COUNT(*) FROM samples) AS sample_count,
    (SELECT COUNT(*) FROM samples WHERE confidence < :confidence_floor)
        AS excluded_low_confidence_count,
    (SELECT COUNT(*) FROM samples WHERE NOT timely) AS excluded_delayed_count,
    (SELECT COUNT(*) FROM samples WHERE source <> 'roboflow_bytetrack') AS excluded_source_count,
    COUNT(*) AS valid_sample_count,
    MIN(occurred_at) AS first_observed_at,
    MAX(occurred_at) AS last_observed_at,
    AVG(person_count) AS person_count_average,
    MIN(person_count) AS person_count_minimum,
    MAX(person_count) AS person_count_maximum,
    COUNT(*) FILTER (WHERE person_count > 0) AS positive_count_samples,
    COUNT(*) FILTER (WHERE person_count = 0) AS zero_count_samples,
    COALESCE((SELECT jsonb_agg(to_jsonb(hourly) ORDER BY hour_start) FROM hourly), '[]'::jsonb)
        AS hourly,
    COALESCE((SELECT jsonb_agg(to_jsonb(event_counts) ORDER BY count DESC, code, severity)
              FROM event_counts), '[]'::jsonb) AS system_event_counts,
    (SELECT manual_request_count FROM requests) AS manual_request_count,
    (SELECT override_request_count FROM requests) AS override_request_count,
    (SELECT urgency_request_count FROM requests) AS urgency_request_count,
    (SELECT automation_request_count FROM requests) AS automation_request_count
FROM valid
"""


def _number(value: Any) -> float | None:
    if value is None:
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _count(row: Mapping[str, Any], key: str) -> int:
    return int(row.get(key) or 0)


def _timestamp(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError("sample summaries require timezone-aware timestamps")
    return value.astimezone(UTC).isoformat()


def _metric(row: Mapping[str, Any], prefix: str, include_endpoints: bool) -> dict[str, Any]:
    fields = (
        ("average", "minimum", "maximum", "first", "last")
        if include_endpoints
        else (
            "average",
            "minimum",
            "maximum",
        )
    )
    return {key: _number(row.get(f"{prefix}_{key}")) for key in fields}


def _hourly(rows: Any, fields: tuple[str, ...]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for row in (rows or [])[:MAX_HOURLY_BUCKETS]:
        result.append(
            {
                "hour_start": _timestamp(row["hour_start"]),
                **{field: _number(row.get(field)) for field in fields},
            }
        )
    return result


def build_analyst_context(
    environment: Mapping[str, Any],
    occupancy: Mapping[str, Any],
    start: datetime,
    end: datetime,
) -> dict[str, Any]:
    """Normalize only allowlisted aggregate fields into JSON primitives."""
    event_counts: list[dict[str, Any]] = []
    for event in (occupancy.get("system_event_counts") or [])[:30]:
        code = event.get("code", "")
        severity = event.get("severity", "")
        if (
            isinstance(code, str)
            and re.fullmatch(r"[a-zA-Z0-9_.:-]{1,80}", code)
            and severity in {"info", "warning", "critical"}
        ):
            event_counts.append(
                {"code": code, "severity": severity, "count": _count(event, "count")}
            )
    return {
        "period_start": _timestamp(start),
        "period_end": _timestamp(end),
        "timezone": "UTC",
        "environment": {
            **{
                key: _count(environment, key)
                for key in (
                    "sample_count",
                    "valid_sample_count",
                    "excluded_delayed_count",
                    "excluded_invalid_count",
                )
            },
            "first_observed_at": _timestamp(environment.get("first_observed_at")),
            "last_observed_at": _timestamp(environment.get("last_observed_at")),
            "temperature_c": _metric(environment, "temperature", True),
            "relative_humidity_pct": _metric(environment, "humidity", True),
            "hourly": _hourly(
                environment.get("hourly"),
                (
                    "sample_count",
                    "temperature_c_average",
                    "temperature_c_minimum",
                    "temperature_c_maximum",
                    "relative_humidity_pct_average",
                ),
            ),
        },
        "occupancy": {
            **{
                key: _count(occupancy, key)
                for key in (
                    "sample_count",
                    "valid_sample_count",
                    "excluded_low_confidence_count",
                    "excluded_delayed_count",
                    "excluded_source_count",
                    "positive_count_samples",
                    "zero_count_samples",
                )
            },
            "confidence_floor": OCCUPANCY_CONFIDENCE_FLOOR,
            "first_observed_at": _timestamp(occupancy.get("first_observed_at")),
            "last_observed_at": _timestamp(occupancy.get("last_observed_at")),
            "person_count": _metric(occupancy, "person_count", False),
            "hourly": _hourly(
                occupancy.get("hourly"),
                (
                    "sample_count",
                    "person_count_average",
                    "person_count_minimum",
                    "person_count_maximum",
                    "positive_count_samples",
                    "zero_count_samples",
                ),
            ),
        },
        "automation_events": {
            "system_event_counts": event_counts,
            **{
                key: _count(occupancy, key)
                for key in (
                    "manual_request_count",
                    "override_request_count",
                    "urgency_request_count",
                    "automation_request_count",
                )
            },
        },
        "limitations": [
            "These are stored observation statistics. Existing environment storage does not "
            "prove publisher provenance or independently distinguish synthetic sensor publishers.",
            "Environment and occupancy summaries are sample-based arithmetic statistics, "
            "not continuous time-weighted coverage or occupied/vacant durations.",
            "Missing samples, low-confidence counts and delayed observations "
            "do not establish vacancy.",
            "Only environment samples received within -5/+30 seconds and camera samples "
            "within -5/+15 seconds of their observation are included in statistics.",
            "Camera samples require source roboflow_bytetrack and confidence at least 0.5. "
            "Excluded category counts overlap and must not be added together.",
            "Hourly sample buckets use UTC. No camera frames, individual tracks "
            "or raw sensor messages "
            "are included; summaries cannot prove hardware performance or an automation failure.",
            "Manual, override, urgency and automation counts describe control requests, "
            "not applied physical changes. Urgency counts may overlap override requests.",
            "System event summaries contain at most 30 machine-code/severity groups. "
            "Free-form messages, credentials and user-authored request reasons are omitted.",
        ],
    }


async def load_analyst_context(
    session: AsyncSession,
    classroom_id: UUID,
    start: datetime,
    end: datetime,
) -> dict[str, Any]:
    """Read bounded aggregates for exactly the evaluated reporting interval."""
    if start.tzinfo is None or end.tzinfo is None or end < start or end - start > MAX_WINDOW:
        raise ValueError("analyst context requires an aware reporting window of at most 32 days")
    if start == end:
        return build_analyst_context({}, {}, start, end)
    parameters = {
        "classroom_id": classroom_id,
        "period_start": start,
        "period_end": end,
        "max_hourly_buckets": MAX_HOURLY_BUCKETS,
        "confidence_floor": OCCUPANCY_CONFIDENCE_FLOOR,
    }
    environment_result = await session.execute(text(ENVIRONMENT_SQL), parameters)
    environment = environment_result.mappings().one()
    occupancy_result = await session.execute(text(OCCUPANCY_SQL), parameters)
    occupancy = occupancy_result.mappings().one()
    return build_analyst_context(dict(environment), dict(occupancy), start, end)
