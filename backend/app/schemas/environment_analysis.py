"""REST contract for database-backed environment analysis."""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import Field

from .common import ContractModel, UtcDateTime

AnalysisStatus = Literal["ready", "insufficient_data"]
FreshnessStatus = Literal["fresh", "stale", "unknown"]
Trend = Literal["rising", "falling", "stable", "unknown"]
InsightLevel = Literal["info", "caution"]


class EnvironmentMetricSummary(ContractModel):
    latest: float | None
    average: float | None
    minimum: float | None
    maximum: float | None
    recent_change: float | None
    trend: Trend


class EnvironmentInsight(ContractModel):
    level: InsightLevel
    code: str
    message: str


class EnvironmentAnalysis(ContractModel):
    classroom_id: UUID
    analysis_status: AnalysisStatus
    freshness: FreshnessStatus
    analysis_method: str = "threshold_rules_v1"
    reading_count: int = Field(ge=0)
    window_start: UtcDateTime
    analyzed_at: UtcDateTime
    latest_observed_at: UtcDateTime | None
    temperature_c: EnvironmentMetricSummary
    relative_humidity_pct: EnvironmentMetricSummary
    illuminance_lux: EnvironmentMetricSummary
    insights: list[EnvironmentInsight]


class LatestEnvironmentTelemetry(ContractModel):
    classroom_id: UUID
    temperature_c: float | None
    relative_humidity_pct: float | None
    illuminance_lux: float | None
    illuminance_adc: int | None
    motion_detected: bool | None
    sample_interval_ms: int | None
    observed_at: UtcDateTime | None
    freshness: FreshnessStatus
