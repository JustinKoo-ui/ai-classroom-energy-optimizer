"""Shared API and realtime contracts.

These are boundary models, not persistence models. Domain policy code should use
framework-free value objects and map to these models at transport boundaries.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Annotated, Any
from uuid import UUID

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, model_validator

JsonObject = dict[str, Any]


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must include a timezone")
    return value.astimezone(timezone.utc)


UtcDateTime = Annotated[datetime, AfterValidator(_as_utc)]
Probability = Annotated[float, Field(ge=0.0, le=1.0)]


class ContractModel(BaseModel):
    """Strict base for all external contracts."""

    model_config = ConfigDict(extra="forbid", use_enum_values=False)


class UserRole(str, Enum):
    VIEWER = "viewer"
    OPERATOR = "operator"
    ADMIN = "admin"


class Freshness(str, Enum):
    FRESH = "fresh"
    STALE = "stale"
    UNKNOWN = "unknown"


class DeviceType(str, Enum):
    AIR_CONDITIONER = "air_conditioner"
    FAN = "fan"
    LIGHTING = "lighting"
    ENVIRONMENT_SENSOR = "environment_sensor"
    ENERGY_METER = "energy_meter"
    CAMERA_GATEWAY = "camera_gateway"
    OTHER = "other"


class DeviceOperationalStatus(str, Enum):
    REGISTERED = "registered"
    ONLINE = "online"
    OFFLINE = "offline"
    FAULT = "fault"
    DISABLED = "disabled"


class OccupancyStatus(str, Enum):
    OCCUPIED = "occupied"
    POSSIBLY_VACANT = "possibly_vacant"
    VACANT_CONFIRMED = "vacant_confirmed"
    UNKNOWN = "unknown"


class ControlTrigger(str, Enum):
    MANUAL = "manual"
    OVERRIDE = "override"
    SCHEDULE = "schedule"
    OCCUPANCY = "occupancy"
    VACANCY = "vacancy"
    COMFORT = "comfort"
    LIGHTING = "lighting"
    AI_RECOMMENDATION = "ai_recommendation"
    SAFETY = "safety"


class DecisionOutcome(str, Enum):
    ALLOWED = "allowed"
    DENIED = "denied"


class CommandAction(str, Enum):
    SET_POWER = "set_power"
    SET_TEMPERATURE = "set_temperature"
    SET_FAN_SPEED = "set_fan_speed"
    SET_BRIGHTNESS = "set_brightness"
    SET_MODE = "set_mode"


class CommandStatus(str, Enum):
    QUEUED = "queued"
    PUBLISHED = "published"
    ACCEPTED = "accepted"
    APPLIED = "applied"
    REJECTED = "rejected"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    CANCELLED = "cancelled"


class RecommendationStatus(str, Enum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    EXPIRED = "expired"
    SUPERSEDED = "superseded"


class OverrideMode(str, Enum):
    FORCE_STATE = "force_state"
    SUSPEND_AUTOMATION = "suspend_automation"


class OverrideStatus(str, Enum):
    SCHEDULED = "scheduled"
    ACTIVE = "active"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


class EventSeverity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class EventStatus(str, Enum):
    OPEN = "open"
    ACKNOWLEDGED = "acknowledged"
    RESOLVED = "resolved"


class UserProfile(ContractModel):
    id: UUID
    email: str
    display_name: str
    role: UserRole


class Site(ContractModel):
    id: UUID
    code: str
    name: str
    timezone: str
    is_active: bool
    created_at: UtcDateTime
    updated_at: UtcDateTime


class Classroom(ContractModel):
    id: UUID
    site_id: UUID
    code: str
    name: str
    capacity: int = Field(gt=0)
    floor: str | None = None
    timezone: str
    control_policy_version: str
    is_active: bool
    created_at: UtcDateTime
    updated_at: UtcDateTime


class DeviceCapabilities(ContractModel):
    supported_actions: list[CommandAction]
    supports_power: bool = False
    setpoint_min_c: float | None = None
    setpoint_max_c: float | None = None
    fan_speed_steps_pct: list[int] = Field(default_factory=list)
    brightness_steps_pct: list[int] = Field(default_factory=list)
    supported_modes: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_ranges(self) -> DeviceCapabilities:
        if (
            self.setpoint_min_c is not None
            and self.setpoint_max_c is not None
            and self.setpoint_min_c > self.setpoint_max_c
        ):
            raise ValueError("setpoint_min_c must not exceed setpoint_max_c")
        if any(step < 0 or step > 100 for step in self.fan_speed_steps_pct):
            raise ValueError("fan speed steps must be between 0 and 100")
        if any(step < 0 or step > 100 for step in self.brightness_steps_pct):
            raise ValueError("brightness steps must be between 0 and 100")
        return self


class Device(ContractModel):
    id: UUID
    classroom_id: UUID
    hardware_uid: str
    name: str
    type: DeviceType
    rated_power_w: float | None = Field(default=None, ge=0)
    capabilities: DeviceCapabilities
    enabled: bool
    created_at: UtcDateTime
    updated_at: UtcDateTime


class DeviceState(ContractModel):
    device_id: UUID
    operational_status: DeviceOperationalStatus
    desired_state: JsonObject = Field(default_factory=dict)
    reported_state: JsonObject = Field(default_factory=dict)
    fault_codes: list[str] = Field(default_factory=list)
    state_version: int = Field(ge=0)
    last_seen_at: UtcDateTime | None = None
    reported_at: UtcDateTime | None = None
    freshness: Freshness
    updated_at: UtcDateTime


class OccupancyState(ContractModel):
    status: OccupancyStatus
    count: int | None = Field(default=None, ge=0)
    confidence: Probability | None = None
    observed_at: UtcDateTime | None = None
    freshness: Freshness

    @model_validator(mode="after")
    def validate_status_count(self) -> OccupancyState:
        if self.status is OccupancyStatus.OCCUPIED and (self.count is None or self.count < 1):
            raise ValueError("occupied state requires count >= 1")
        if self.status is OccupancyStatus.VACANT_CONFIRMED and self.count != 0:
            raise ValueError("vacant_confirmed state requires count = 0")
        return self


class EnvironmentState(ContractModel):
    temperature_c: float
    relative_humidity_pct: float = Field(ge=0, le=100)
    illuminance_lux: float = Field(ge=0)
    observed_at: UtcDateTime
    freshness: Freshness


class DeparturePrediction(ContractModel):
    id: UUID
    classroom_id: UUID
    horizon_seconds: int = Field(default=60, ge=60, le=60)
    leave_probability: Probability
    model_version: str
    feature_set_version: str
    predicted_at: UtcDateTime
    expires_at: UtcDateTime

    @model_validator(mode="after")
    def validate_expiry(self) -> DeparturePrediction:
        if self.expires_at <= self.predicted_at:
            raise ValueError("expires_at must be after predicted_at")
        return self


class AIRecommendation(ContractModel):
    id: UUID
    classroom_id: UUID
    device_id: UUID | None = None
    prediction_id: UUID | None = None
    recommended_action: CommandAction
    recommended_value: JsonObject
    reason: str
    confidence: Probability | None = None
    status: RecommendationStatus
    created_at: UtcDateTime
    expires_at: UtcDateTime

    @model_validator(mode="after")
    def validate_expiry(self) -> AIRecommendation:
        if self.expires_at <= self.created_at:
            raise ValueError("expires_at must be after created_at")
        return self


class ControlDecisionSummary(ContractModel):
    id: UUID
    request_id: UUID
    outcome: DecisionOutcome
    reason_codes: list[str] = Field(min_length=1)
    summary: str
    matched_rule: str
    bounded_action: CommandAction | None = None
    bounded_value: JsonObject | None = None
    policy_version: str
    evaluated_at: UtcDateTime

    @model_validator(mode="after")
    def validate_bounded_output(self) -> ControlDecisionSummary:
        has_output = self.bounded_action is not None and self.bounded_value is not None
        if self.outcome is DecisionOutcome.ALLOWED and not has_output:
            raise ValueError("allowed decision requires bounded output")
        if self.outcome is DecisionOutcome.DENIED and (
            self.bounded_action is not None or self.bounded_value is not None
        ):
            raise ValueError("denied decision cannot include bounded output")
        return self


class DeviceCommand(ContractModel):
    id: UUID
    classroom_id: UUID
    device_id: UUID
    decision_id: UUID
    action: CommandAction
    value: JsonObject
    status: CommandStatus
    retry_count: int = Field(default=0, ge=0)
    created_at: UtcDateTime
    updated_at: UtcDateTime
    expires_at: UtcDateTime


class ManualOverride(ContractModel):
    id: UUID
    classroom_id: UUID
    device_id: UUID
    created_by: UUID
    mode: OverrideMode
    value: JsonObject | None = None
    reason: str
    status: OverrideStatus
    starts_at: UtcDateTime
    ends_at: UtcDateTime
    created_at: UtcDateTime
    updated_at: UtcDateTime

    @model_validator(mode="after")
    def validate_override(self) -> ManualOverride:
        if self.ends_at <= self.starts_at:
            raise ValueError("ends_at must be after starts_at")
        if self.mode is OverrideMode.FORCE_STATE and self.value is None:
            raise ValueError("force_state override requires value")
        if self.mode is OverrideMode.SUSPEND_AUTOMATION and self.value is not None:
            raise ValueError("suspend_automation override does not accept value")
        return self


class Schedule(ContractModel):
    id: UUID
    classroom_id: UUID
    device_id: UUID
    name: str
    action: CommandAction
    value: JsonObject
    rrule: str
    timezone: str
    starts_at: UtcDateTime
    ends_at: UtcDateTime | None = None
    max_lateness_seconds: int = Field(ge=0)
    enabled: bool
    version: int = Field(ge=1)
    next_run_at: UtcDateTime | None = None
    created_at: UtcDateTime
    updated_at: UtcDateTime

    @model_validator(mode="after")
    def validate_period(self) -> Schedule:
        if self.ends_at is not None and self.ends_at <= self.starts_at:
            raise ValueError("ends_at must be after starts_at")
        return self


class EnergyUpdate(ContractModel):
    classroom_id: UUID
    device_id: UUID | None = None
    active_power_w: float = Field(ge=0)
    period_energy_wh: float = Field(ge=0)
    period_started_at: UtcDateTime
    observed_at: UtcDateTime
    freshness: Freshness


class SystemEvent(ContractModel):
    id: UUID
    classroom_id: UUID | None = None
    device_id: UUID | None = None
    severity: EventSeverity
    status: EventStatus
    code: str
    message: str
    details: JsonObject = Field(default_factory=dict)
    occurred_at: UtcDateTime


class ClassroomSnapshot(ContractModel):
    classroom_id: UUID
    occupancy: OccupancyState
    environment: EnvironmentState | None
    devices: list[DeviceState]
    latest_prediction: DeparturePrediction | None
    active_overrides: list[ManualOverride]
    computed_at: UtcDateTime
