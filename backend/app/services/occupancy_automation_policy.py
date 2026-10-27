"""Pure occupancy-aware relay policy for the local classroom prototype."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from math import isfinite

# AC qualification starts only after the camera confirms at least three people.
OCCUPANCY_THRESHOLD = 3
AC_DELAY = timedelta(seconds=5)
PIR_SCAN_DURATION = timedelta(seconds=20)
URGENCY_DURATION = timedelta(minutes=5)
LDR_THRESHOLD = 1000
TEMPERATURE_THRESHOLD_C = 26.0
HUMIDITY_THRESHOLD_PCT = 65.0

DEVICE_NAMES = ("fan", "lighting", "air_conditioner")


@dataclass(frozen=True)
class DevicePolicyState:
    on: bool
    mode: str


@dataclass(frozen=True)
class AutomationState:
    qualifying_since: datetime | None = None
    below_threshold_since: datetime | None = None
    pir_scan_until: datetime | None = None
    fallback_lights_latched: bool = False
    last_motion_detected: bool | None = None
    camera_was_fresh: bool = False
    urgency_started_at: datetime | None = None
    urgency_until: datetime | None = None
    urgency_restore_states: dict[str, DevicePolicyState] | None = None


@dataclass(frozen=True)
class AutomationInputs:
    now: datetime
    occupancy_count: int | None
    occupancy_fresh: bool
    temperature_c: float | None
    humidity_pct: float | None
    environment_fresh: bool
    ldr_adc: int | None
    pir_motion: bool | None
    pir_fresh: bool
    devices: dict[str, DevicePolicyState]


@dataclass(frozen=True)
class AutomationDecision:
    state: AutomationState
    targets: dict[str, bool | None]
    sources: dict[str, str]
    restore_manual: dict[str, bool]
    urgency_status: str


def begin_urgency(
    state: AutomationState,
    devices: dict[str, DevicePolicyState],
    now: datetime,
) -> AutomationState:
    return replace(
        state,
        urgency_started_at=now,
        urgency_until=now + URGENCY_DURATION,
        urgency_restore_states={name: devices[name] for name in DEVICE_NAMES},
    )


def evaluate_automation(
    state: AutomationState,
    inputs: AutomationInputs,
) -> AutomationDecision:
    now = inputs.now
    occupancy_count = inputs.occupancy_count
    occupancy_known = (
        inputs.occupancy_fresh
        and isinstance(occupancy_count, int)
        and not isinstance(occupancy_count, bool)
        and occupancy_count >= 0
    )
    qualifies = (
        occupancy_known
        and occupancy_count is not None
        and occupancy_count >= OCCUPANCY_THRESHOLD
    )
    camera_available = occupancy_known
    qualifying_since = state.qualifying_since
    below_threshold_since = state.below_threshold_since
    pir_scan_until = state.pir_scan_until
    fallback_latched = state.fallback_lights_latched
    urgency_started_at = state.urgency_started_at
    urgency_until = state.urgency_until
    urgency_restore_states = state.urgency_restore_states
    targets: dict[str, bool | None] = {name: None for name in DEVICE_NAMES}
    sources: dict[str, str] = {name: "automation" for name in DEVICE_NAMES}
    restore_manual: dict[str, bool] = {}
    urgency_status = "inactive"

    if urgency_until is not None and now < urgency_until:
        for name in DEVICE_NAMES:
            targets[name] = True
            sources[name] = "urgency"
        urgency_status = "monitoring"
    else:
        urgency_expired = urgency_until is not None
        if urgency_expired:
            urgency_started_at = None
            urgency_until = None
            if occupancy_known and occupancy_count is not None and occupancy_count == 0:
                for name in DEVICE_NAMES:
                    targets[name] = False
                    sources[name] = "urgency_timeout"
                urgency_status = "completed_no_presence"
                qualifying_since = None
                below_threshold_since = now
            else:
                urgency_status = (
                    "completed_presence" if occupancy_known else "completed_camera_unknown"
                )
                for name, prior in (urgency_restore_states or {}).items():
                    targets[name] = prior.on
                    sources[name] = "urgency_restore"
                    if prior.mode == "manual":
                        restore_manual[name] = prior.on
                        sources[name] = "manual_restore"
            urgency_restore_states = None

        # Urgency owns its expiry/restoration evaluation. Normal automation
        # resumes on the next tick, without overwriting those priority targets.
        if urgency_status == "inactive":
            occupied = occupancy_known and occupancy_count is not None and occupancy_count >= 1
            scan_active = pir_scan_until is not None and now < pir_scan_until
            motion_detected = inputs.pir_fresh and inputs.pir_motion is True
            motion_event = motion_detected and state.last_motion_detected is not True
            ldr_valid = (
                inputs.environment_fresh
                and isinstance(inputs.ldr_adc, int)
                and not isinstance(inputs.ldr_adc, bool)
                and 0 <= inputs.ldr_adc <= 4095
            )
            dark = ldr_valid and inputs.ldr_adc is not None and inputs.ldr_adc > LDR_THRESHOLD
            light_from_sensors = motion_detected and dark

            # Keep legacy persisted fields schema-compatible, but do not retain
            # the previous vacancy grace or indefinite fallback lighting latch.
            below_threshold_since = None
            fallback_latched = False
            if qualifies:
                if qualifying_since is None or qualifying_since > now:
                    qualifying_since = now
            else:
                qualifying_since = None

            targets = {name: False for name in DEVICE_NAMES}
            # PIR is the first lighting gate. Neither a scan nor camera
            # occupancy may bypass fresh motion AND a fresh ADC above 1000.
            targets["lighting"] = light_from_sensors
            if occupied:
                # Any person cancels the scan; lighting still follows its gates.
                pir_scan_until = None
                targets["fan"] = True

                qualifying_duration = (
                    now - qualifying_since if qualifying_since is not None else None
                )
                if (
                    qualifying_duration is not None
                    and qualifying_duration >= AC_DELAY
                    and inputs.environment_fresh
                ):
                    temperature_high = (
                        _valid_reading(inputs.temperature_c, -20.0, 80.0)
                        and inputs.temperature_c is not None
                        and inputs.temperature_c >= TEMPERATURE_THRESHOLD_C
                    )
                    humidity_high = (
                        _valid_reading(inputs.humidity_pct, 0.0, 100.0)
                        and inputs.humidity_pct is not None
                        and inputs.humidity_pct >= HUMIDITY_THRESHOLD_PCT
                    )
                    targets["air_conditioner"] = temperature_high or humidity_high
            else:
                if not scan_active:
                    pir_scan_until = None
                    if motion_event:
                        pir_scan_until = now + PIR_SCAN_DURATION
                if pir_scan_until is not None or (
                    state.pir_scan_until is not None and not light_from_sensors
                ):
                    sources["lighting"] = "pir_fallback"
                if occupancy_known:
                    sources["fan"] = "vacancy"
                    sources["air_conditioner"] = "vacancy"

        if urgency_status == "inactive":
            urgency_status = "inactive"

    # Normal AC qualification must not span a period owned by an override,
    # during which the normal occupancy sequence was not being evaluated.
    if urgency_status != "inactive":
        qualifying_since = None

    next_state = AutomationState(
        qualifying_since=qualifying_since,
        below_threshold_since=below_threshold_since,
        pir_scan_until=pir_scan_until,
        fallback_lights_latched=fallback_latched,
        last_motion_detected=(
            inputs.pir_motion
            if inputs.pir_fresh and isinstance(inputs.pir_motion, bool)
            else state.last_motion_detected
        ),
        camera_was_fresh=camera_available,
        urgency_started_at=urgency_started_at,
        urgency_until=urgency_until,
        urgency_restore_states=urgency_restore_states,
    )
    return AutomationDecision(
        state=next_state,
        targets=targets,
        sources=sources,
        restore_manual=restore_manual,
        urgency_status=urgency_status,
    )


def _valid_reading(value: float | None, minimum: float, maximum: float) -> bool:
    return (
        value is not None
        and not isinstance(value, bool)
        and isfinite(value)
        and minimum <= value <= maximum
    )
