"""Numerical analytics contracts, independent of any language model."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import Field

from app.schemas.common import ContractModel


class RuntimeMetrics(ContractModel):
    on_seconds: float = Field(ge=0)
    off_seconds: float = Field(ge=0)
    unknown_seconds: float = Field(ge=0)
    elapsed_seconds: float = Field(ge=0)
    coverage_pct: float = Field(ge=0, le=100)
    on_pct: float = Field(ge=0, le=100)
    activations: int = Field(ge=0)
    transitions: int = Field(ge=0)
    average_on_seconds: float | None
    longest_on_seconds: float = Field(ge=0)


class RuntimeBucket(RuntimeMetrics):
    start: datetime
    end: datetime
    label: str


class EnergyMetrics(ContractModel):
    estimated_energy_kwh: float
    daily_24h_baseline_kwh: float
    period_baseline_kwh: float
    theoretical_difference_kwh: float
    provisional_difference_pct: float | None
    final_savings_pct: float | None
    is_complete: bool


class DeviceAnalytics(ContractModel):
    key: Literal["lighting", "fan", "air_conditioner"]
    device_id: UUID
    name: str
    indicator: Literal["yellow", "blue", "red"]
    assumed_power_w: float
    tracking_started_at: datetime | None
    first_observed_at: datetime | None
    last_observed_at: datetime | None
    source: str = "esp32_live_state"
    timestamp_accuracy: str = "Snapshot observation; transition time is approximate."
    runtime: RuntimeMetrics
    energy: EnergyMetrics
    hourly: list[RuntimeBucket]
    daily: list[RuntimeBucket]
    manual_observation_segments: int
    fault_observation_segments: int


class AnalyticsReport(ContractModel):
    schema_version: str = "1.0"
    methodology_version: str = "assumed_power_bounded_observation_v1"
    classroom_id: UUID
    period: Literal["day", "week", "month"]
    timezone: str
    period_start: datetime
    period_end: datetime
    evaluated_until: datetime
    as_of: datetime
    calendar_days: int
    period_elapsed_seconds: float
    tracking_started_at: datetime | None
    stale_after_seconds: int
    is_partial: bool
    devices: list[DeviceAnalytics]
    total_energy: EnergyMetrics
    warnings: list[str]
    limitations: list[str]
