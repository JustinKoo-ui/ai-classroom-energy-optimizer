"""Versioned MQTT envelopes and payloads."""

from __future__ import annotations

from enum import StrEnum
from typing import Generic, TypeVar
from uuid import UUID

from pydantic import Field, model_validator

from .common import (
    CommandAction,
    ContractModel,
    JsonObject,
    Probability,
    UtcDateTime,
)


PayloadT = TypeVar("PayloadT")


class MqttEnvelope(ContractModel, Generic[PayloadT]):
    schema_version: str = Field(pattern=r"^1\.[0-9]+$")
    message_id: UUID
    occurred_at: UtcDateTime
    site_id: UUID
    classroom_id: UUID
    producer_id: str = Field(min_length=1, max_length=200)
    payload: PayloadT


class EnvironmentTelemetryPayload(ContractModel):
    sensor_id: str
    temperature_c: float = Field(ge=-20, le=80)
    relative_humidity_pct: float = Field(ge=0, le=100)
    illuminance_lux: float = Field(ge=0, le=200_000)
    sample_interval_ms: int = Field(gt=0)


class EnvironmentTelemetryPayloadV2(ContractModel):
    sensor_id: str = Field(min_length=1, max_length=200)
    temperature_c: float = Field(ge=-20, le=80)
    relative_humidity_pct: float = Field(ge=0, le=100)
    illuminance_adc: int = Field(ge=0, le=4095)
    motion_detected: bool | None = None
    sample_interval_ms: int = Field(gt=0)


class MqttEnvelopeV2(ContractModel, Generic[PayloadT]):
    schema_version: str = Field(pattern=r"^2\.[0-9]+$")
    message_id: UUID
    occurred_at: UtcDateTime
    site_id: UUID
    classroom_id: UUID
    producer_id: str = Field(min_length=1, max_length=200)
    payload: PayloadT


class PowerTelemetryPayload(ContractModel):
    device_id: UUID
    voltage_v: float | None = Field(default=None, ge=0, le=300)
    current_a: float | None = Field(default=None, ge=0)
    active_power_w: float = Field(ge=0)
    apparent_power_va: float | None = Field(default=None, ge=0)
    power_factor: float | None = Field(default=None, ge=0, le=1)
    energy_wh_total: float | None = Field(default=None, ge=0)
    sample_interval_ms: int = Field(gt=0)


class OccupancyObservationPayload(ContractModel):
    camera_id: str
    count: int = Field(ge=0)
    confidence: Probability
    entries_delta: int = Field(default=0, ge=0)
    exits_delta: int = Field(default=0, ge=0)
    frame_sequence: int = Field(ge=0)
    source: str = "roboflow_bytetrack"


class AvailabilityStatus(StrEnum):
    ONLINE = "online"
    OFFLINE = "offline"


class AvailabilityPayload(ContractModel):
    status: AvailabilityStatus
    boot_id: UUID
    firmware_version: str = Field(min_length=1, max_length=100)


class DeviceStatePayload(ContractModel):
    device_id: UUID
    state: JsonObject
    fault_codes: list[str] = Field(default_factory=list)
    state_version: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_relay_state(self) -> DeviceStatePayload:
        if not isinstance(self.state.get("on"), bool):
            raise ValueError("relay state must include a boolean 'on' field")
        if self.state.get("mode") not in {"auto", "manual"}:
            raise ValueError("relay state mode must be 'auto' or 'manual'")
        return self


class ApprovedCommandPayload(ContractModel):
    command_id: UUID
    decision_id: UUID
    device_id: UUID
    action: CommandAction
    value: JsonObject
    expires_at: UtcDateTime
    idempotency_key: str = Field(min_length=1, max_length=200)


class AcknowledgementStatus(StrEnum):
    ACCEPTED = "accepted"
    APPLIED = "applied"
    REJECTED = "rejected"
    FAILED = "failed"


class CommandAcknowledgementPayload(ContractModel):
    command_id: UUID
    device_id: UUID
    status: AcknowledgementStatus
    reason_code: str | None = None
    reported_state: JsonObject | None = None
    applied_at: UtcDateTime | None = None

    @model_validator(mode="after")
    def validate_applied_state(self) -> CommandAcknowledgementPayload:
        if self.status is AcknowledgementStatus.APPLIED and (
            self.reported_state is None or self.applied_at is None
        ):
            raise ValueError("applied acknowledgement requires reported_state and applied_at")
        return self
