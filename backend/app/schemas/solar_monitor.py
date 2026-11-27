"""MQTT and API contracts for the independently monitored solar tracker."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import AwareDatetime, BeforeValidator, Field, StrictBool, model_validator

from app.schemas.classroom_telemetry import (
    ClassroomEnergyPoint,
    ClassroomMeterPoint,
    ClassroomTelemetryReading,
)
from app.schemas.common import ContractModel, Freshness, UtcDateTime

ProducerId = Annotated[str, Field(pattern=r"^[A-Za-z0-9_-]{1,80}$")]
FiniteNumber = Annotated[float, Field(strict=True, allow_inf_nan=False)]


def _parse_iso_timestamp(value: object) -> datetime | None:
    if isinstance(value, str):
        return datetime.fromisoformat(value)
    if value is None or isinstance(value, datetime):
        return value
    raise ValueError("timestamp must be a timezone-aware ISO 8601 string or null")


class SolarEnvelope(ContractModel):
    schema_version: Literal["1.0"]
    message_id: UUID
    occurred_at: Annotated[UtcDateTime | None, BeforeValidator(_parse_iso_timestamp)]
    site_id: UUID
    classroom_id: UUID
    producer_id: ProducerId


class SolarGPS(ContractModel):
    enabled: StrictBool = True
    fix: StrictBool
    time_valid: StrictBool
    latitude: FiniteNumber | None = Field(ge=-90, le=90)
    longitude: FiniteNumber | None = Field(ge=-180, le=180)
    last_fix_age_ms: int | None = Field(strict=True, ge=0)

    @model_validator(mode="after")
    def validate_fix(self) -> SolarGPS:
        if not self.enabled and (self.fix or self.time_valid or self.last_fix_age_ms is not None):
            raise ValueError("disabled GPS cannot report a fix, GPS time, or fix age")
        if self.fix and (self.latitude is None or self.longitude is None):
            raise ValueError("current GPS fix requires both coordinates")
        if not self.fix and (self.latitude is not None or self.longitude is not None):
            raise ValueError("coordinates require a current GPS fix")
        return self


class SolarYawSequence(ContractModel):
    kind: Literal["test", "run"]
    phase: Literal["idle", "clockwise", "pause", "anticlockwise"]
    leg_progress_deg: FiniteNumber | None
    leg_target_deg: FiniteNumber | None
    net_rotation_deg: FiniteNumber
    result: (
        Literal[
            "completed",
            "user_stopped",
            "feedback_unavailable",
            "sampling_interrupted",
            "wrong_direction",
            "stalled",
            "timed_out",
        ]
        | None
    )


class SolarFixedYaw(ContractModel):
    clock_set: StrictBool
    referenced: StrictBool
    calibrating: StrictBool
    armed: StrictBool
    reachable: StrictBool
    status: Literal[
        "waiting_for_clock",
        "waiting_for_reference",
        "calibrating",
        "ready",
        "tracking",
        "aligned",
        "sun_below_horizon",
        "outside_travel",
        "feedback_unavailable",
        "reference_expired",
        "base_moved_during_calibration",
        "sampling_interrupted",
        "control_delayed",
        "wrong_direction",
        "stalled",
    ]
    demo_speed: int = Field(strict=True, ge=1, le=600)
    solar_time: Annotated[AwareDatetime | None, BeforeValidator(_parse_iso_timestamp)]


class SolarTracking(ContractModel):
    mode: Literal["auto", "manual"]
    target_azimuth: FiniteNumber | None
    actual_azimuth: FiniteNumber | None = Field(ge=0, le=360)
    target_zenith: FiniteNumber | None
    actual_zenith: FiniteNumber | None = Field(ge=0, le=180)
    azimuth_pwm: int = Field(strict=True, ge=0, le=255)
    zenith_pwm: int = Field(strict=True, ge=0, le=255)
    stop_reason: (
        Literal[
            "waiting_for_gps",
            "waiting_for_clock",
            "spa_error",
            "feedback_unavailable",
            "zenith_limit",
            "local_hold",
        ]
        | None
    )
    yaw_sequence: SolarYawSequence | None = None
    fixed_yaw: SolarFixedYaw | None = None


class SolarPower(ContractModel):
    voltage: FiniteNumber | None = Field(ge=0)
    current: FiniteNumber | None
    power: FiniteNumber | None
    energy: FiniteNumber | None = Field(ge=0)


class SolarSensors(ContractModel):
    ina219_available: StrictBool
    azimuth_mpu_available: StrictBool
    zenith_mpu_available: StrictBool


class SolarTelemetry(ContractModel):
    boot_id: UUID
    gps: SolarGPS
    tracking: SolarTracking
    solar_power: SolarPower
    sensors: SolarSensors

    @model_validator(mode="after")
    def validate_sensor_samples(self) -> SolarTelemetry:
        if not self.sensors.ina219_available and any(
            value is not None
            for value in (
                self.solar_power.voltage,
                self.solar_power.current,
                self.solar_power.power,
            )
        ):
            raise ValueError("electrical measurements require an available INA219")
        if (
            not self.sensors.azimuth_mpu_available
            and self.tracking.actual_azimuth is not None
        ) or (
            not self.sensors.zenith_mpu_available
            and self.tracking.actual_zenith is not None
        ):
            raise ValueError("actual angles require an available feedback sensor")
        return self


class SolarAvailability(ContractModel):
    boot_id: UUID
    status: Literal["online", "offline"]


class SolarTelemetryEnvelope(SolarEnvelope):
    payload: SolarTelemetry


class SolarStatusEnvelope(SolarEnvelope):
    payload: SolarAvailability


class SolarReading(ContractModel):
    message_id: UUID
    received_at: UtcDateTime
    occurred_at: UtcDateTime | None
    payload: SolarTelemetry


class PrototypeHealth(ContractModel):
    availability: Literal["online", "offline", "unknown"]
    freshness: Freshness
    last_received_at: UtcDateTime | None


class SolarHealth(PrototypeHealth):
    telemetry: SolarReading | None


class PrototypeSnapshot(ContractModel):
    broker_connected: bool
    solar: SolarHealth
    relay: PrototypeHealth
    history: list[SolarReading] | None = None
    classroom_telemetry: ClassroomTelemetryReading | None = None
    classroom_telemetry_freshness: Freshness = Freshness.UNKNOWN
    energy_history: list[ClassroomEnergyPoint] | None = None
    classroom_meter_history: list[ClassroomMeterPoint] | None = None
