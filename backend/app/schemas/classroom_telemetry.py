"""MQTT and monitoring contracts for the classroom ESP32 sensor snapshot."""

from __future__ import annotations

from datetime import datetime
from math import isclose
from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, StrictBool, field_validator, model_validator

from app.schemas.common import ContractModel, UtcDateTime

FiniteNumber = Annotated[float, Field(strict=True, allow_inf_nan=False)]


class ClassroomRelayCommands(ContractModel):
    light: StrictBool
    fan: StrictBool
    air_conditioner: StrictBool


class ClassroomSensorStatus(ContractModel):
    dht22_available: StrictBool
    pir_ready: StrictBool
    bh1750_available: StrictBool
    bh1750_fresh: StrictBool
    bh1750_age_ms: int | None = Field(strict=True, ge=0)
    ina219_available: StrictBool


class ClassroomTelemetry(ContractModel):
    sensor_id: str = Field(min_length=1, max_length=200)
    boot_id: UUID
    uptime_ms: int = Field(strict=True, ge=0)
    sample_interval_ms: int = Field(strict=True, gt=0, le=2**31 - 1)
    temperature_c: FiniteNumber | None = Field(ge=-40, le=80)
    relative_humidity_pct: FiniteNumber | None = Field(ge=0, le=100)
    illuminance_lux: FiniteNumber | None = Field(ge=0, le=200_000)
    motion_detected: StrictBool | None
    voltage_v: FiniteNumber | None = Field(ge=0, le=26)
    current_a: FiniteNumber | None
    active_power_w: FiniteNumber | None
    energy_wh_total: FiniteNumber | None = Field(ge=0)
    energy_kwh_total: FiniteNumber | None = Field(ge=0)
    relays: ClassroomRelayCommands
    sensors: ClassroomSensorStatus
    energy_storage_available: StrictBool

    @model_validator(mode="after")
    def validate_availability(self) -> ClassroomTelemetry:
        sensors = self.sensors
        if not sensors.dht22_available and (
            self.temperature_c is not None or self.relative_humidity_pct is not None
        ):
            raise ValueError("temperature and humidity readings require an available DHT22")
        if sensors.pir_ready != (self.motion_detected is not None):
            raise ValueError("motion status is available only when the PIR is ready")
        has_lux = self.illuminance_lux is not None
        if has_lux != (sensors.bh1750_age_ms is not None):
            raise ValueError("a BH1750 reading requires its age")
        if sensors.bh1750_fresh and not (has_lux and sensors.bh1750_available):
            raise ValueError("a fresh lux reading requires an available BH1750")
        if not sensors.ina219_available and any(
            value is not None for value in (self.voltage_v, self.current_a, self.active_power_w)
        ):
            raise ValueError("electrical readings require an available INA219")
        if self.energy_wh_total is None or self.energy_kwh_total is None:
            if self.energy_wh_total is not None or self.energy_kwh_total is not None:
                raise ValueError("accumulated energy requires both Wh and kWh")
        elif not isclose(
            self.energy_wh_total / 1000, self.energy_kwh_total, rel_tol=1e-4, abs_tol=1e-8
        ):
            raise ValueError("kWh must match the reported Wh total")
        return self


class ClassroomTelemetryEnvelope(ContractModel):
    schema_version: Literal["3.0"]
    message_id: UUID
    occurred_at: UtcDateTime | None
    site_id: UUID
    classroom_id: UUID
    producer_id: str = Field(min_length=1, max_length=200)
    payload: ClassroomTelemetry

    @field_validator("occurred_at", mode="before")
    @classmethod
    def validate_timestamp(cls, value: object) -> object:
        if isinstance(value, str):
            return datetime.fromisoformat(value)
        if value is None or isinstance(value, datetime):
            return value
        raise ValueError("timestamp must be a timezone-aware ISO 8601 string or null")


class ClassroomTelemetryReading(ContractModel):
    message_id: UUID
    received_at: UtcDateTime
    occurred_at: UtcDateTime | None
    payload: ClassroomTelemetry


class ClassroomEnergyPoint(ContractModel):
    received_at: UtcDateTime
    energy_wh_total: FiniteNumber = Field(ge=0)


class ClassroomMeterPoint(ContractModel):
    received_at: UtcDateTime
    boot_id: UUID
    active_power_w: FiniteNumber | None
    energy_wh_total: FiniteNumber | None = Field(ge=0)
