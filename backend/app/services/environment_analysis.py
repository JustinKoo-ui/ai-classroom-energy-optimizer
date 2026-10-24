"""Build deterministic findings from stored environment telemetry."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Literal
from uuid import UUID

from app.schemas.environment_analysis import (
    EnvironmentAnalysis,
    EnvironmentInsight,
    EnvironmentMetricSummary,
    FreshnessStatus,
)

Trend = Literal["rising", "falling", "stable", "unknown"]


def build_environment_analysis(
    *,
    classroom_id: UUID,
    reading_count: int,
    analyzed_at: datetime,
    latest_observed_at: datetime | None,
    temperature: tuple[float | None, float | None, float | None, float | None, float | None],
    humidity: tuple[float | None, float | None, float | None, float | None, float | None],
    illuminance: tuple[float | None, float | None, float | None, float | None, float | None],
) -> EnvironmentAnalysis:
    """Build a transparent analysis; insights are never translated into commands."""
    enough_data = reading_count >= 3
    freshness = _freshness(latest_observed_at, analyzed_at)
    insights: list[EnvironmentInsight] = []

    if not enough_data:
        insights.append(
            EnvironmentInsight(
                level="info",
                code="insufficient_readings",
                message=(
                    f"Only {reading_count} environment reading(s) were stored in the last "
                    "24 hours. At least 3 are needed for an initial trend summary."
                ),
            )
        )
    else:
        average_temperature = temperature[1]
        if average_temperature is not None and average_temperature > 26.5:
            insights.append(
                EnvironmentInsight(
                    level="caution",
                    code="temperature_above_target_band",
                    message=(
                        f"Stored temperature readings averaged {average_temperature:.1f} °C, "
                        "above the occupied comfort target band of 25.5–26.5 °C. Occupancy "
                        "is not included in this analysis; review room conditions before "
                        "taking action."
                    ),
                )
            )
        elif average_temperature is not None and average_temperature < 25.5:
            insights.append(
                EnvironmentInsight(
                    level="info",
                    code="temperature_below_target_band",
                    message=(
                        f"Stored temperature readings averaged {average_temperature:.1f} °C, "
                        "below the occupied comfort target band of 25.5–26.5 °C. Occupancy "
                        "is not included in this analysis."
                    ),
                )
            )

        average_illuminance = illuminance[1]
        if average_illuminance is not None and average_illuminance < 270:
            insights.append(
                EnvironmentInsight(
                    level="caution",
                    code="illuminance_below_target",
                    message=(
                        f"Stored illuminance readings averaged {average_illuminance:.0f} lux, "
                        "below the 270 lux review threshold for the 300 lux lighting target. "
                        "Occupancy and daylight are not included; check these before action."
                    ),
                )
            )
        elif average_illuminance is None:
            insights.append(
                EnvironmentInsight(
                    level="info",
                    code="illuminance_not_calibrated",
                    message=(
                        "Light readings are not available in calibrated lux, so the lighting "
                        "threshold cannot be evaluated."
                    ),
                )
            )

        if not insights:
            insights.append(
                EnvironmentInsight(
                    level="info",
                    code="no_threshold_findings",
                    message=(
                        "The stored temperature and illuminance averages did not cross the "
                        "configured review thresholds during this window."
                    ),
                )
            )

    return EnvironmentAnalysis(
        classroom_id=classroom_id,
        analysis_status="ready" if enough_data else "insufficient_data",
        freshness=freshness,
        reading_count=reading_count,
        window_start=analyzed_at - timedelta(hours=24),
        analyzed_at=analyzed_at,
        latest_observed_at=latest_observed_at,
        temperature_c=_metric_summary(temperature, stable_delta=0.1),
        relative_humidity_pct=_metric_summary(humidity, stable_delta=1.0),
        illuminance_lux=_metric_summary(illuminance, stable_delta=10.0),
        insights=insights,
    )


def _metric_summary(
    values: tuple[float | None, float | None, float | None, float | None, float | None],
    *,
    stable_delta: float,
) -> EnvironmentMetricSummary:
    latest, average, minimum, maximum, previous = values
    change = latest - previous if latest is not None and previous is not None else None
    if change is None:
        trend: Trend = "unknown"
    elif abs(change) < stable_delta:
        trend = "stable"
    elif change > 0:
        trend = "rising"
    else:
        trend = "falling"
    return EnvironmentMetricSummary(
        latest=latest,
        average=average,
        minimum=minimum,
        maximum=maximum,
        recent_change=change,
        trend=trend,
    )


def _freshness(
    latest_observed_at: datetime | None,
    analyzed_at: datetime,
) -> FreshnessStatus:
    if latest_observed_at is None:
        return "unknown"
    return "fresh" if analyzed_at - latest_observed_at <= timedelta(seconds=30) else "stale"
