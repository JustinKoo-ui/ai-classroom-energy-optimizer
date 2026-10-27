"""Deterministic, assumed-wattage estimates. No measured power or AI inputs."""

from __future__ import annotations

import math
from datetime import datetime
from uuid import UUID
from zoneinfo import ZoneInfo

from app.domain.local_hardware import LOCAL_RELAY_DEVICES
from app.domain.runtime import Period, Segment, reconstruct, runtime_buckets, summarize
from app.repositories.runtime_history import RuntimeHistory
from app.schemas.analytics import AnalyticsReport, DeviceAnalytics, EnergyMetrics, RuntimeMetrics

LIMITATIONS = [
    "All energy values are estimates from assumed watts and estimated ON time, not measurements.",
    "ESP32 reports its software relay state; physical appliance operation "
    "is not independently verified.",
    "Snapshot timestamps are observations. Changes are assigned to the later observation; "
    "their exact transition time is unknown, bounded by the preceding fresh observation.",
    "Unchanged observations are compressed. State is carried forward only up to the freshness "
    "limit; gaps and time before tracking began remain UNKNOWN, never OFF.",
    "Activation counts include observed OFF-to-ON changes only. Initial ON states and changes "
    "across gaps cannot establish activations. ON-run statistics are clipped to observed coverage.",
    "The baseline is a hypothetical 24-hour all-ON day per calendar day. Provisional differences "
    "compare incomplete estimates with that full baseline and are not final daily savings.",
    "No calibrated meter, electricity-cost estimate, verified carbon estimate "
    "or causal savings claim.",
]


def calculate_energy(
    assumed_power_w: float,
    on_seconds: float,
    elapsed_seconds: float,
    unknown_seconds: float,
    calendar_days: int,
    period_finished: bool,
) -> EnergyMetrics:
    values = (assumed_power_w, on_seconds, elapsed_seconds, unknown_seconds)
    if not all(math.isfinite(value) and value >= 0 for value in values):
        raise ValueError("power and durations must be finite and non-negative")
    if calendar_days < 1 or on_seconds + unknown_seconds > elapsed_seconds + 0.001:
        raise ValueError("inconsistent runtime duration; investigate observation data")
    estimated = assumed_power_w * (on_seconds / 3600) / 1000
    daily = assumed_power_w * 24 / 1000
    baseline = daily * calendar_days
    difference = baseline - estimated  # Negative values deliberately preserved.
    percentage = difference / baseline * 100 if baseline > 0 else None
    complete = period_finished and unknown_seconds <= 0.001
    return EnergyMetrics(
        estimated_energy_kwh=estimated,
        daily_24h_baseline_kwh=daily,
        period_baseline_kwh=baseline,
        theoretical_difference_kwh=difference,
        provisional_difference_pct=percentage,
        final_savings_pct=percentage if complete else None,
        is_complete=complete,
    )


def build_analytics_report(
    *,
    history: RuntimeHistory,
    classroom_id: UUID,
    start: datetime,
    end: datetime,
    now: datetime,
    period: Period,
    powers: dict[str, float],
    stale_after_seconds: int = 15,
) -> AnalyticsReport:
    zone = ZoneInfo(history.timezone)
    evaluated_until = max(start, min(now, end))
    elapsed = (evaluated_until - start).total_seconds()
    days = (end.astimezone(zone).date() - start.astimezone(zone).date()).days
    devices: list[DeviceAnalytics] = []
    warnings: list[str] = []
    if now < end:
        warnings.append("partial_period: reporting calendar period is not finished")
    if history.tracking_started_at is None:
        warnings.append("no_history: no reliable live observations have been recorded")
    elif history.tracking_started_at > start:
        warnings.append("tracking_started_during_or_after_period: earlier time is UNKNOWN")
    for key in ("lighting", "fan", "air_conditioner"):
        definition = LOCAL_RELAY_DEVICES[key]
        rows = [row for row in history.segments if row["device_id"] == definition["id"]]
        segments = [
            Segment(
                row["state"],
                row["first_observed_at"],
                row["last_observed_at"],
                row["mode"],
                row.get("freshness_limit_seconds"),
            )
            for row in rows
        ]
        intervals = reconstruct(segments, start, evaluated_until, stale_after_seconds)
        runtime = RuntimeMetrics.model_validate(summarize(intervals, start, evaluated_until))
        if any(
            row.get("freshness_limit_seconds", stale_after_seconds) != stale_after_seconds
            for row in rows
        ):
            warnings.append(f"{key}: historical segments use their recorded freshness limits")
        if runtime.unknown_seconds:
            warnings.append(f"{key}: incomplete coverage; UNKNOWN time is excluded from energy")
        energy = calculate_energy(
            powers[key], runtime.on_seconds, elapsed, runtime.unknown_seconds, days, now >= end
        )
        if energy.theoretical_difference_kwh < 0:
            warnings.append(f"{key}: negative theoretical difference; investigate runtime/baseline")
        # A per-device tracking start can lie outside the requested report.
        tracking_starts = getattr(history, "tracking_starts_by_device", {})
        tracking_start = tracking_starts.get(definition["id"])
        if tracking_start is None and rows:
            tracking_start = min(row["first_observed_at"] for row in rows)
        devices.append(
            DeviceAnalytics(
                key=key,
                device_id=definition["id"],
                name=definition["name"],
                indicator={"lighting": "yellow", "fan": "blue", "air_conditioner": "red"}[key],
                assumed_power_w=powers[key],
                tracking_started_at=tracking_start,
                first_observed_at=min((row["first_observed_at"] for row in rows), default=None),
                last_observed_at=max((row["last_observed_at"] for row in rows), default=None),
                runtime=runtime,
                energy=energy,
                hourly=runtime_buckets(intervals, start, evaluated_until, history.timezone, True),
                daily=runtime_buckets(intervals, start, evaluated_until, history.timezone, False),
                manual_observation_segments=sum(row["mode"] == "manual" for row in rows),
                fault_observation_segments=sum(bool(row["quality"]) for row in rows),
            )
        )
    # Energy sums per device; combined coverage is represented by device-seconds.
    total = EnergyMetrics(
        estimated_energy_kwh=sum(d.energy.estimated_energy_kwh for d in devices),
        daily_24h_baseline_kwh=sum(d.energy.daily_24h_baseline_kwh for d in devices),
        period_baseline_kwh=sum(d.energy.period_baseline_kwh for d in devices),
        theoretical_difference_kwh=sum(d.energy.theoretical_difference_kwh for d in devices),
        provisional_difference_pct=None,
        final_savings_pct=None,
        is_complete=all(d.energy.is_complete for d in devices),
    )
    if total.period_baseline_kwh > 0:
        total.provisional_difference_pct = (
            total.theoretical_difference_kwh / total.period_baseline_kwh * 100
        )
        if total.is_complete:
            total.final_savings_pct = total.provisional_difference_pct
    return AnalyticsReport(
        classroom_id=classroom_id,
        period=period,
        timezone=history.timezone,
        period_start=start,
        period_end=end,
        evaluated_until=evaluated_until,
        as_of=now,
        calendar_days=days,
        period_elapsed_seconds=elapsed,
        tracking_started_at=history.tracking_started_at,
        stale_after_seconds=stale_after_seconds,
        is_partial=not total.is_complete,
        devices=devices,
        total_energy=total,
        warnings=warnings,
        limitations=LIMITATIONS,
    )
