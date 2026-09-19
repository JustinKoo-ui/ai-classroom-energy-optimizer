"""REST request and response models."""

from __future__ import annotations

from typing import Generic, TypeVar
from uuid import UUID

from pydantic import Field, model_validator

from .common import (
    CommandAction,
    CommandStatus,
    ContractModel,
    DecisionOutcome,
    DeviceCapabilities,
    DeviceType,
    JsonObject,
    OverrideMode,
    UtcDateTime,
)

T = TypeVar("T")


class Page(ContractModel, Generic[T]):
    items: list[T]
    next_cursor: str | None = None


class ErrorBody(ContractModel):
    code: str
    message: str
    details: JsonObject = Field(default_factory=dict)
    trace_id: UUID


class ErrorResponse(ContractModel):
    error: ErrorBody


class SiteCreate(ContractModel):
    code: str = Field(min_length=1, max_length=100)
    name: str = Field(min_length=1, max_length=200)
    timezone: str


class SiteUpdate(ContractModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    timezone: str | None = None
    is_active: bool | None = None


class ClassroomCreate(ContractModel):
    site_id: UUID
    code: str = Field(min_length=1, max_length=100)
    name: str = Field(min_length=1, max_length=200)
    capacity: int = Field(gt=0)
    floor: str | None = None
    timezone: str
    control_policy_id: UUID


class ClassroomUpdate(ContractModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    capacity: int | None = Field(default=None, gt=0)
    floor: str | None = None
    timezone: str | None = None
    is_active: bool | None = None


class DeviceCreate(ContractModel):
    hardware_uid: str = Field(min_length=1, max_length=200)
    mqtt_client_id: str = Field(min_length=1, max_length=200)
    name: str = Field(min_length=1, max_length=200)
    type: DeviceType
    rated_power_w: float | None = Field(default=None, ge=0)
    capabilities: DeviceCapabilities


class DeviceUpdate(ContractModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    rated_power_w: float | None = Field(default=None, ge=0)
    capabilities: DeviceCapabilities | None = None
    enabled: bool | None = None


class ManualCommandRequest(ContractModel):
    action: CommandAction
    value: JsonObject
    reason: str = Field(min_length=3, max_length=500)
    expires_in_seconds: int = Field(default=30, ge=1, le=300)


class ManualCommandSubmission(ContractModel):
    request_id: UUID
    decision_id: UUID
    decision: DecisionOutcome
    reason_codes: list[str] = Field(min_length=1)
    command_id: UUID | None = None
    command_status: CommandStatus | None = None

    @model_validator(mode="after")
    def validate_allowed_command(self) -> ManualCommandSubmission:
        if self.decision is DecisionOutcome.ALLOWED and self.command_id is None:
            raise ValueError("allowed decision requires command_id")
        if self.decision is DecisionOutcome.DENIED and (
            self.command_id is not None or self.command_status is not None
        ):
            raise ValueError("denied decision cannot include a command")
        return self


class ManualOverrideCreate(ContractModel):
    mode: OverrideMode
    value: JsonObject | None = None
    starts_at: UtcDateTime
    ends_at: UtcDateTime
    reason: str = Field(min_length=3, max_length=500)

    @model_validator(mode="after")
    def validate_override(self) -> ManualOverrideCreate:
        if self.ends_at <= self.starts_at:
            raise ValueError("ends_at must be after starts_at")
        if self.mode is OverrideMode.FORCE_STATE and self.value is None:
            raise ValueError("force_state override requires value")
        if self.mode is OverrideMode.SUSPEND_AUTOMATION and self.value is not None:
            raise ValueError("suspend_automation override does not accept value")
        return self


class ScheduleCreate(ContractModel):
    device_id: UUID
    name: str = Field(min_length=1, max_length=200)
    action: CommandAction
    value: JsonObject
    rrule: str = Field(min_length=1, max_length=1000)
    timezone: str
    starts_at: UtcDateTime
    ends_at: UtcDateTime | None = None
    max_lateness_seconds: int = Field(default=60, ge=0, le=3600)
    enabled: bool = True

    @model_validator(mode="after")
    def validate_period(self) -> ScheduleCreate:
        if self.ends_at is not None and self.ends_at <= self.starts_at:
            raise ValueError("ends_at must be after starts_at")
        return self


class ScheduleUpdate(ContractModel):
    expected_version: int = Field(ge=1)
    name: str | None = Field(default=None, min_length=1, max_length=200)
    action: CommandAction | None = None
    value: JsonObject | None = None
    rrule: str | None = Field(default=None, min_length=1, max_length=1000)
    timezone: str | None = None
    starts_at: UtcDateTime | None = None
    ends_at: UtcDateTime | None = None
    max_lateness_seconds: int | None = Field(default=None, ge=0, le=3600)
    enabled: bool | None = None


class EnergySummary(ContractModel):
    classroom_id: UUID
    period_start: UtcDateTime
    period_end: UtcDateTime
    energy_wh: float = Field(ge=0)
    average_power_w: float = Field(ge=0)
    peak_power_w: float = Field(ge=0)
    estimated_cost: float | None = None
    currency: str | None = Field(default=None, min_length=3, max_length=3)
    by_device: dict[str, float] = Field(default_factory=dict)


class EnergySavingsEstimate(ContractModel):
    id: UUID
    classroom_id: UUID
    device_id: UUID | None = None
    period_start: UtcDateTime
    period_end: UtcDateTime
    baseline_method: str
    baseline_version: str
    baseline_energy_wh: float = Field(ge=0)
    actual_energy_wh: float = Field(ge=0)
    estimated_savings_wh: float
    estimated_cost_savings: float | None = None
    currency: str | None = Field(default=None, min_length=3, max_length=3)
    quality_score: float | None = Field(default=None, ge=0, le=1)
    methodology_notes: str | None = None
    calculated_at: UtcDateTime
