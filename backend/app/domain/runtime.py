"""Read-only runtime reconstruction from bounded firmware observations."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Literal
from zoneinfo import ZoneInfo

State = Literal["on", "off", "unknown"]
Period = Literal["day", "week", "month"]


@dataclass(frozen=True)
class Segment:
    state: State
    first_observed_at: datetime
    last_observed_at: datetime
    mode: str = "unknown"
    freshness_limit_seconds: int | None = None

    def __post_init__(self) -> None:
        for value in (self.first_observed_at, self.last_observed_at):
            if value.tzinfo is None or value.utcoffset() is None:
                raise ValueError("runtime timestamps must be timezone aware")
        if self.last_observed_at < self.first_observed_at:
            raise ValueError("segment end precedes its start")
        if self.state not in {"on", "off", "unknown"}:
            raise ValueError("invalid runtime state")
        if (
            self.freshness_limit_seconds is not None
            and not 2 <= self.freshness_limit_seconds <= 300
        ):
            raise ValueError("invalid recorded freshness limit")


@dataclass(frozen=True)
class Interval:
    start: datetime
    end: datetime
    state: State


def period_bounds(day: date, period: Period, timezone: str) -> tuple[datetime, datetime]:
    """Calendar day, ISO Monday week, or calendar month, returned in UTC."""
    zone = ZoneInfo(timezone)
    if period == "week":
        day -= timedelta(days=day.weekday())
        next_day = day + timedelta(days=7)
    elif period == "month":
        day = day.replace(day=1)
        next_day = (day.replace(day=28) + timedelta(days=4)).replace(day=1)
    elif period == "day":
        next_day = day + timedelta(days=1)
    else:
        raise ValueError("invalid reporting period")
    return (
        datetime.combine(day, time.min, zone).astimezone(UTC),
        datetime.combine(next_day, time.min, zone).astimezone(UTC),
    )


def reconstruct(
    segments: list[Segment], start: datetime, end: datetime, stale_after_seconds: int = 15
) -> list[Interval]:
    """Return non-overlapping estimated ON/OFF/UNKNOWN intervals, including gaps."""
    if start.tzinfo is None or end.tzinfo is None or end < start or stale_after_seconds <= 0:
        raise ValueError("invalid runtime reporting interval")
    ordered = sorted(segments, key=lambda s: s.first_observed_at)
    result: list[Interval] = []
    cursor = start
    for index, segment in enumerate(ordered):
        first = segment.first_observed_at.astimezone(UTC)
        last = segment.last_observed_at.astimezone(UTC)
        if index and first <= ordered[index - 1].last_observed_at:
            raise ValueError("overlapping runtime segments")
        supported_end = last + timedelta(
            seconds=segment.freshness_limit_seconds or stale_after_seconds,
        )
        if index + 1 < len(ordered):
            supported_end = min(supported_end, ordered[index + 1].first_observed_at)
        left, right = max(start, first), min(end, supported_end)
        if supported_end == start and first < start:
            # Preserve an observed transition exactly at reporting midnight.
            result.append(Interval(first, start, segment.state))
        if right <= left:
            continue
        if left > cursor:
            result.append(Interval(cursor, left, "unknown"))
        result.append(Interval(left, right, segment.state))
        cursor = right
    if cursor < end:
        result.append(Interval(cursor, end, "unknown"))
    # A mode change is not an electrical transition and does not break an ON run.
    merged: list[Interval] = []
    for item in result:
        if merged and merged[-1].state == item.state and merged[-1].end == item.start:
            previous = merged.pop()
            merged.append(Interval(previous.start, item.end, item.state))
        else:
            merged.append(item)
    return merged


def summarize(
    intervals: list[Interval],
    start: datetime,
    end: datetime,
) -> dict[str, float | int | None]:
    """Counts exclude initial ON and transitions across unobserved gaps."""
    seconds = {"on": 0.0, "off": 0.0, "unknown": 0.0}
    on_runs: list[float] = []
    activations = transitions = 0
    previous: Interval | None = None
    for item in intervals:
        if (
            previous is not None
            and start <= item.start < end
            and previous.end == item.start
            and previous.state != "unknown"
            and item.state != "unknown"
            and previous.state != item.state
        ):
            transitions += 1
            activations += item.state == "on"
        previous = item
        left, right = max(start, item.start), min(end, item.end)
        if right <= left:
            continue
        duration = (right - left).total_seconds()
        seconds[item.state] += duration
        if item.state == "on":
            on_runs.append(duration)
    elapsed = (end - start).total_seconds()
    if elapsed < 0 or abs(sum(seconds.values()) - elapsed) > 0.001:
        raise ValueError("inconsistent reconstructed runtime duration")
    known = seconds["on"] + seconds["off"]
    return {
        "on_seconds": seconds["on"],
        "off_seconds": seconds["off"],
        "unknown_seconds": seconds["unknown"],
        "elapsed_seconds": elapsed,
        # Duration consistency is checked above; cap only floating-point overshoot.
        "coverage_pct": min(100.0, 100 * known / elapsed) if elapsed else 0,
        "on_pct": min(100.0, 100 * seconds["on"] / elapsed) if elapsed else 0,
        "activations": activations,
        "transitions": transitions,
        "average_on_seconds": sum(on_runs) / len(on_runs) if on_runs else None,
        "longest_on_seconds": max(on_runs, default=0),
    }


def runtime_buckets(
    intervals: list[Interval], start: datetime, end: datetime, timezone: str, hourly: bool
) -> list[dict[str, object]]:
    buckets: list[dict[str, object]] = []
    cursor = start
    zone = ZoneInfo(timezone)
    while cursor < end:
        if hourly:
            boundary = cursor + timedelta(hours=1)
        else:
            next_day = cursor.astimezone(zone).date() + timedelta(days=1)
            boundary = datetime.combine(next_day, time.min, zone).astimezone(UTC)
        boundary = min(boundary, end)
        buckets.append(
            {
                "start": cursor,
                "end": boundary,
                "label": cursor.astimezone(zone).isoformat(),
                **summarize(intervals, cursor, boundary),
            }
        )
        cursor = boundary
    return buckets
