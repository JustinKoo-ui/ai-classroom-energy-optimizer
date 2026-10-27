from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.services.occupancy_automation_policy import (
    AC_DELAY,
    DEVICE_NAMES,
    LDR_THRESHOLD,
    OCCUPANCY_THRESHOLD,
    PIR_SCAN_DURATION,
    URGENCY_DURATION,
    AutomationInputs,
    AutomationState,
    DevicePolicyState,
    begin_urgency,
    evaluate_automation,
)

NOW = datetime(2026, 10, 25, 8, 0, tzinfo=UTC)


def _inputs(
    *,
    now: datetime = NOW,
    count: int | None = 3,
    occupancy_fresh: bool = True,
    temperature: float | None = 27.0,
    humidity: float | None = 66.0,
    environment_fresh: bool = True,
    ldr: int | None = 1000,
    pir: bool | None = False,
    pir_fresh: bool = True,
    states: dict[str, DevicePolicyState] | None = None,
) -> AutomationInputs:
    return AutomationInputs(
        now=now,
        occupancy_count=count,
        occupancy_fresh=occupancy_fresh,
        temperature_c=temperature,
        humidity_pct=humidity,
        environment_fresh=environment_fresh,
        ldr_adc=ldr,
        pir_motion=pir,
        pir_fresh=pir_fresh,
        devices=states or {name: DevicePolicyState(on=False, mode="auto") for name in DEVICE_NAMES},
    )


def test_main_flow_parameters_match_requested_boundaries() -> None:
    assert OCCUPANCY_THRESHOLD == 3
    assert AC_DELAY.total_seconds() == 5
    assert LDR_THRESHOLD == 1000
    assert PIR_SCAN_DURATION.total_seconds() == 20
    assert URGENCY_DURATION.total_seconds() == 300


@pytest.mark.parametrize("count", [1, 2, 3, 4, 5, 9])
def test_confirmed_person_turns_fan_on_without_waiting(count: int) -> None:
    decision = evaluate_automation(AutomationState(), _inputs(count=count))

    assert decision.targets["fan"] is True
    assert decision.targets["air_conditioner"] is False
    assert decision.state.qualifying_since == (NOW if count >= 3 else None)


@pytest.mark.parametrize(
    ("count", "fresh"),
    [(0, True), (None, False), (None, True), (3, False), (5, False),
     (-1, True), (True, True)],
)
def test_waiting_without_confirmed_occupancy_requests_all_outputs_off(
    count: int | None,
    fresh: bool,
) -> None:
    devices = {name: DevicePolicyState(on=True, mode="auto") for name in DEVICE_NAMES}
    decision = evaluate_automation(
        AutomationState(qualifying_since=NOW - timedelta(minutes=1)),
        _inputs(count=count, occupancy_fresh=fresh, states=devices),
    )

    assert decision.targets == {name: False for name in DEVICE_NAMES}
    assert decision.state.qualifying_since is None


def test_valid_zero_remains_distinct_from_unknown_camera_state() -> None:
    zero = evaluate_automation(AutomationState(), _inputs(count=0))
    unknown = evaluate_automation(
        AutomationState(),
        _inputs(count=None, occupancy_fresh=False),
    )

    assert zero.state.camera_was_fresh is True
    assert unknown.state.camera_was_fresh is False


@pytest.mark.parametrize("count", [1.0, 3.0, 5.0, -1.0, float("nan"), float("inf")])
def test_non_integer_camera_counts_are_unknown_instead_of_occupancy(count: float) -> None:
    decision = evaluate_automation(
        AutomationState(qualifying_since=NOW - timedelta(seconds=31)),
        _inputs(count=count),  # type: ignore[arg-type]
    )

    assert decision.targets == {name: False for name in DEVICE_NAMES}
    assert decision.state.camera_was_fresh is False
    assert decision.state.qualifying_since is None


@pytest.mark.parametrize(
    ("ldr", "expected"),
    [(0, False), (861, False), (999, False), (1000, False), (1001, True), (4095, True)],
)
def test_occupied_lighting_requires_adc_above_one_thousand_with_motion(
    ldr: int,
    expected: bool,
) -> None:
    decision = evaluate_automation(AutomationState(), _inputs(count=1, ldr=ldr, pir=True))

    assert decision.targets["lighting"] is expected


@pytest.mark.parametrize("ldr", [None, -1, 4096, True])
def test_invalid_ldr_keeps_normal_lighting_off_even_with_motion(ldr: int | None) -> None:
    decision = evaluate_automation(AutomationState(), _inputs(count=1, ldr=ldr, pir=True))

    assert decision.targets["fan"] is True
    assert decision.targets["lighting"] is False


def test_old_persisted_lighting_latch_does_not_bypass_normal_ldr_control() -> None:
    decision = evaluate_automation(
        AutomationState(fallback_lights_latched=True),
        _inputs(count=1, ldr=50),
    )

    assert decision.targets["lighting"] is False
    assert decision.state.fallback_lights_latched is False


@pytest.mark.parametrize(("count", "fresh"), [(0, True), (1, True), (None, False)])
@pytest.mark.parametrize("ldr", [50, 861, 999, 1000, 1001, 4095])
@pytest.mark.parametrize("motion", [False, True])
def test_automatic_light_requires_ldr_and_motion_independently_of_camera_count(
    count: int | None,
    fresh: bool,
    ldr: int,
    motion: bool,
) -> None:
    # A sustained HIGH supplies the motion input without starting a new scan.
    decision = evaluate_automation(
        AutomationState(last_motion_detected=True),
        _inputs(count=count, occupancy_fresh=fresh, ldr=ldr, pir=motion),
    )

    assert decision.targets["lighting"] is (ldr > 1000 and motion)
    assert decision.targets["fan"] is (count == 1)
    assert decision.targets["air_conditioner"] is False
    assert decision.state.pir_scan_until is None


@pytest.mark.parametrize(("count", "fresh"), [(0, True), (1, True), (None, False)])
@pytest.mark.parametrize(
    ("ldr", "ldr_fresh", "motion", "motion_fresh", "expected"),
    [
        (4095, False, True, True, False),
        (4095, True, False, False, False),
        (4095, False, False, True, False),
        (4095, False, True, False, False),
        (50, True, True, False, False),
        (50, True, False, True, False),
        (None, True, True, True, False),
        (1001, False, True, True, False),
        (1001, True, True, False, False),
        (1001, True, True, True, True),
        (4095, True, True, True, True),
    ],
)
def test_each_lighting_input_requires_its_own_valid_fresh_evidence(
    count: int | None,
    fresh: bool,
    ldr: int | None,
    ldr_fresh: bool,
    motion: bool,
    motion_fresh: bool,
    expected: bool,
) -> None:
    decision = evaluate_automation(
        AutomationState(last_motion_detected=True),
        _inputs(
            count=count,
            occupancy_fresh=fresh,
            ldr=ldr,
            environment_fresh=ldr_fresh,
            pir=motion,
            pir_fresh=motion_fresh,
        ),
    )
    assert decision.targets["lighting"] is expected
    assert decision.targets["fan"] is (count == 1)
    assert decision.targets["air_conditioner"] is False


def test_ac_requires_continuous_occupancy_for_at_least_five_seconds() -> None:
    started = evaluate_automation(AutomationState(), _inputs(count=3))
    continuing = evaluate_automation(
        started.state,
        _inputs(now=NOW + timedelta(seconds=4, milliseconds=999), count=4),
    )
    boundary = evaluate_automation(
        continuing.state,
        _inputs(now=NOW + timedelta(seconds=5), count=3),
    )
    elapsed = evaluate_automation(
        boundary.state,
        _inputs(now=NOW + timedelta(seconds=5, milliseconds=1), count=3),
    )

    assert started.state.qualifying_since == NOW
    assert continuing.state.qualifying_since == NOW
    assert continuing.targets["air_conditioner"] is False
    assert boundary.targets["air_conditioner"] is True
    assert elapsed.targets["air_conditioner"] is True


@pytest.mark.parametrize(
    ("temperature", "humidity", "expected"),
    [
        (27.0, 50.0, True),
        (24.0, 70.0, True),
        (27.0, 70.0, True),
        (26.0, 65.0, True),
        (26.0, 50.0, True),
        (24.0, 65.0, True),
        (25.9, 64.9, False),
        (26.1, 65.0, True),
        (26.0, 65.1, True),
    ],
)
@pytest.mark.parametrize("count", [0, 1, 2, 3, 4, 5])
def test_qualified_ac_uses_either_environment_threshold(
    temperature: float,
    humidity: float,
    expected: bool,
    count: int,
) -> None:
    decision = evaluate_automation(
        AutomationState(qualifying_since=NOW - timedelta(seconds=31)),
        _inputs(count=count, temperature=temperature, humidity=humidity),
    )

    assert decision.targets["air_conditioner"] is (count >= 3 and expected)


@pytest.mark.parametrize(
    ("temperature", "humidity", "expected"),
    [
        (27.0, None, True),
        (None, 70.0, True),
        (26.0, None, True),
        (None, 65.0, True),
        (27.0, float("nan"), True),
        (float("nan"), 70.0, True),
        (27.0, float("inf"), True),
        (float("inf"), 70.0, True),
        (27.0, 101.0, True),
        (-21.0, 70.0, True),
        (None, None, False),
        (float("nan"), float("nan"), False),
        (float("inf"), 50.0, False),
        (24.0, float("inf"), False),
        (81.0, 50.0, False),
        (24.0, 101.0, False),
        (-21.0, 65.0, True),
        (26.0, -1.0, True),
        (True, 65.0, True),
        (26.0, True, True),
        (-21.0, 64.9, False),
        (25.9, -1.0, False),
        (True, 64.9, False),
        (25.9, True, False),
    ],
)
def test_ac_independently_validates_each_dht_reading(
    temperature: float | None,
    humidity: float | None,
    expected: bool,
) -> None:
    decision = evaluate_automation(
        AutomationState(qualifying_since=NOW - timedelta(seconds=31)),
        _inputs(temperature=temperature, humidity=humidity),
    )

    assert decision.targets["air_conditioner"] is expected


def test_stale_environment_cannot_satisfy_ac_or_normal_lighting_thresholds() -> None:
    decision = evaluate_automation(
        AutomationState(qualifying_since=NOW - timedelta(seconds=31)),
        _inputs(environment_fresh=False, ldr=0, temperature=80.0, humidity=100.0),
    )

    assert decision.targets["fan"] is True
    assert decision.targets["lighting"] is False
    assert decision.targets["air_conditioner"] is False


@pytest.mark.parametrize(
    ("count", "fresh"),
    [(0, True), (1, True), (2, True), (None, False), (3, False), (5, False)],
)
def test_loss_of_qualifying_occupancy_resets_ac_immediately_and_requires_a_new_full_period(
    count: int | None,
    fresh: bool,
) -> None:
    reset = evaluate_automation(
        AutomationState(qualifying_since=NOW - timedelta(seconds=31)),
        _inputs(count=count, occupancy_fresh=fresh),
    )
    resumed_at = NOW + timedelta(seconds=1)
    resumed = evaluate_automation(reset.state, _inputs(now=resumed_at, count=3))
    before_five = evaluate_automation(
        resumed.state,
        _inputs(now=resumed_at + timedelta(seconds=4, milliseconds=999), count=3),
    )
    at_five = evaluate_automation(
        before_five.state,
        _inputs(now=resumed_at + timedelta(seconds=5), count=3),
    )

    assert reset.state.qualifying_since is None
    assert reset.targets["air_conditioner"] is False
    assert resumed.state.qualifying_since == resumed_at
    assert resumed.targets["air_conditioner"] is False
    assert before_five.targets["air_conditioner"] is False
    assert at_five.targets["air_conditioner"] is True


def test_future_qualifying_timestamp_is_restarted_instead_of_reused() -> None:
    decision = evaluate_automation(
        AutomationState(qualifying_since=NOW + timedelta(seconds=10)),
        _inputs(),
    )

    assert decision.state.qualifying_since == NOW
    assert decision.targets["air_conditioner"] is False


@pytest.mark.parametrize(("count", "fresh"), [(0, True), (None, False), (5, False)])
def test_rising_pir_starts_scan_for_zero_or_unknown_occupancy_but_not_fan_or_ac(
    count: int | None,
    fresh: bool,
) -> None:
    decision = evaluate_automation(
        AutomationState(last_motion_detected=False),
        _inputs(count=count, occupancy_fresh=fresh, pir=True, ldr=4095),
    )

    assert decision.state.pir_scan_until == NOW + timedelta(seconds=20)
    assert decision.targets == {"fan": False, "lighting": True, "air_conditioner": False}
    assert decision.sources["lighting"] == "pir_fallback"


def test_stale_pir_does_not_start_scan_or_rearm_sustained_high() -> None:
    state = AutomationState(last_motion_detected=True)
    stale = evaluate_automation(
        state,
        _inputs(count=0, pir=False, pir_fresh=False),
    )
    still_high = evaluate_automation(
        stale.state,
        _inputs(now=NOW + timedelta(seconds=1), count=0, pir=True),
    )

    assert stale.state.pir_scan_until is None
    assert stale.state.last_motion_detected is True
    assert still_high.state.pir_scan_until is None


@pytest.mark.parametrize("motion", [None, 0, 1, "unknown"])
def test_missing_or_invalid_pir_reading_does_not_rearm_sustained_high(motion: object) -> None:
    previous_high = AutomationState(last_motion_detected=True)
    invalid = evaluate_automation(
        previous_high,
        _inputs(count=0, pir=motion, pir_fresh=True, ldr=4095),  # type: ignore[arg-type]
    )
    still_high = evaluate_automation(
        invalid.state,
        _inputs(now=NOW + timedelta(seconds=1), count=0, pir=True, ldr=4095),
    )

    assert invalid.state.last_motion_detected is True
    assert invalid.state.pir_scan_until is None
    assert invalid.targets == {name: False for name in DEVICE_NAMES}
    assert still_high.state.pir_scan_until is None
    assert still_high.targets == {"fan": False, "lighting": True, "air_conditioner": False}


def test_sustained_high_does_not_extend_or_restart_scan_and_fresh_low_rearms_it() -> None:
    started = evaluate_automation(AutomationState(), _inputs(count=0, pir=True, ldr=4095))
    ongoing = evaluate_automation(
        started.state,
        _inputs(
            now=NOW + PIR_SCAN_DURATION - timedelta(milliseconds=1),
            count=0,
            pir=True,
            ldr=4095,
        ),
    )
    expired = evaluate_automation(
        ongoing.state,
        _inputs(now=NOW + PIR_SCAN_DURATION, count=0, pir=True, ldr=4095),
    )
    waiting = evaluate_automation(
        expired.state,
        _inputs(now=NOW + timedelta(seconds=22), count=0, pir=True, ldr=4095),
    )
    rearmed = evaluate_automation(
        waiting.state,
        _inputs(now=NOW + timedelta(seconds=23), count=0, pir=False, ldr=4095),
    )
    restarted = evaluate_automation(
        rearmed.state,
        _inputs(now=NOW + timedelta(seconds=24), count=0, pir=True, ldr=4095),
    )

    assert ongoing.state.pir_scan_until == NOW + PIR_SCAN_DURATION
    assert ongoing.targets["lighting"] is True
    assert expired.targets == {"fan": False, "lighting": True, "air_conditioner": False}
    assert expired.state.pir_scan_until is None
    assert waiting.targets == {"fan": False, "lighting": True, "air_conditioner": False}
    assert waiting.state.pir_scan_until is None
    assert rearmed.targets == {name: False for name in DEVICE_NAMES}
    assert restarted.state.pir_scan_until == NOW + timedelta(seconds=44)
    assert restarted.targets["lighting"] is True


def test_scan_does_not_hold_light_after_motion_ends_even_with_high_adc() -> None:
    started = evaluate_automation(
        AutomationState(),
        _inputs(count=0, pir=True, ldr=4095),
    )
    scan_holding = evaluate_automation(
        started.state,
        _inputs(now=NOW + timedelta(seconds=19), count=0, pir=False, ldr=4095),
    )
    expired = evaluate_automation(
        scan_holding.state,
        _inputs(now=NOW + timedelta(seconds=20), count=0, pir=False, ldr=4095),
    )
    waiting = evaluate_automation(
        expired.state,
        _inputs(now=NOW + timedelta(seconds=22), count=0, pir=False, ldr=4095),
    )

    assert started.targets["lighting"] is True
    assert scan_holding.state.pir_scan_until == NOW + PIR_SCAN_DURATION
    assert scan_holding.targets["lighting"] is False
    assert expired.state.pir_scan_until is None
    assert expired.targets == {name: False for name in DEVICE_NAMES}
    assert waiting.targets == {name: False for name in DEVICE_NAMES}


@pytest.mark.parametrize("count", [0, 1])
def test_no_motion_turns_light_off_during_active_scan_or_occupancy_handoff(count: int) -> None:
    scan_until = NOW + PIR_SCAN_DURATION
    decision = evaluate_automation(
        AutomationState(last_motion_detected=True, pir_scan_until=scan_until),
        _inputs(now=NOW + timedelta(seconds=1), count=count, pir=False, ldr=4095),
    )

    assert decision.targets["lighting"] is False
    assert decision.targets["fan"] is (count == 1)
    assert decision.targets["air_conditioner"] is False
    assert decision.state.pir_scan_until == (scan_until if count == 0 else None)


def test_motion_low_immediately_turns_off_previous_automatic_light_with_high_adc() -> None:
    illuminated = evaluate_automation(
        AutomationState(),
        _inputs(count=1, pir=True, ldr=4095),
    )
    motion_ended = evaluate_automation(
        illuminated.state,
        _inputs(now=NOW + timedelta(seconds=1), count=1, pir=False, ldr=4095),
    )

    assert illuminated.targets["lighting"] is True
    assert motion_ended.targets["lighting"] is False
    assert motion_ended.targets["fan"] is True
    assert motion_ended.targets["air_conditioner"] is False


def test_new_motion_during_existing_scan_does_not_extend_its_deadline() -> None:
    started = evaluate_automation(AutomationState(), _inputs(count=0, pir=True))
    low = evaluate_automation(
        started.state,
        _inputs(now=NOW + timedelta(seconds=2), count=0, pir=False),
    )
    high = evaluate_automation(
        low.state,
        _inputs(now=NOW + timedelta(seconds=3), count=0, pir=True),
    )

    assert high.state.pir_scan_until == NOW + PIR_SCAN_DURATION


@pytest.mark.parametrize("count", [1, 5])
def test_person_during_scan_cancels_timeout_but_cannot_bypass_lighting_inputs(count: int) -> None:
    scanning = evaluate_automation(
        AutomationState(),
        _inputs(count=0, pir=True, ldr=4095),
    )
    recovered = evaluate_automation(
        scanning.state,
        _inputs(now=NOW + timedelta(seconds=2), count=count, pir=False, ldr=4095),
    )
    normal = evaluate_automation(
        recovered.state,
        _inputs(now=NOW + timedelta(seconds=3), count=count, pir=True, ldr=50),
    )
    past_cancelled_timeout = evaluate_automation(
        normal.state,
        _inputs(now=NOW + timedelta(seconds=21), count=count, pir=True, ldr=4095),
    )

    assert recovered.state.pir_scan_until is None
    assert recovered.targets["fan"] is True
    assert recovered.targets["lighting"] is False
    assert recovered.targets["air_conditioner"] is False
    assert normal.targets["lighting"] is False
    assert past_cancelled_timeout.targets["lighting"] is True


def test_camera_availability_change_does_not_create_motion_event_while_pir_stays_high() -> None:
    state = AutomationState(last_motion_detected=True, camera_was_fresh=True)
    decision = evaluate_automation(
        state,
        _inputs(count=None, occupancy_fresh=False, pir=True, ldr=4095),
    )

    assert decision.state.pir_scan_until is None
    assert decision.targets == {"fan": False, "lighting": True, "air_conditioner": False}


def test_urgency_forces_outputs_on_then_shuts_off_only_for_fresh_zero() -> None:
    devices = {name: DevicePolicyState(on=False, mode="auto") for name in DEVICE_NAMES}
    active_state = begin_urgency(AutomationState(), devices, NOW)
    active = evaluate_automation(active_state, _inputs(now=NOW + timedelta(minutes=4)))
    expired_zero = evaluate_automation(
        active.state,
        _inputs(now=NOW + URGENCY_DURATION, count=0),
    )
    expired_presence = evaluate_automation(
        active.state,
        _inputs(now=NOW + URGENCY_DURATION, count=1),
    )
    expired_unknown = evaluate_automation(
        active.state,
        _inputs(
            now=NOW + URGENCY_DURATION,
            count=None,
            occupancy_fresh=False,
        ),
    )

    assert active.targets == {name: True for name in DEVICE_NAMES}
    assert expired_zero.targets == {name: False for name in DEVICE_NAMES}
    assert expired_presence.urgency_status == "completed_presence"
    assert expired_unknown.urgency_status == "completed_camera_unknown"
    assert expired_unknown.targets == {name: False for name in DEVICE_NAMES}


def test_urgency_restores_manual_targets_without_changing_manual_mode() -> None:
    devices = {
        "fan": DevicePolicyState(on=True, mode="manual"),
        "lighting": DevicePolicyState(on=False, mode="manual"),
        "air_conditioner": DevicePolicyState(on=False, mode="auto"),
    }
    active = begin_urgency(AutomationState(), devices, NOW)
    expired = evaluate_automation(
        active,
        _inputs(now=NOW + URGENCY_DURATION, count=1, states=devices),
    )

    assert expired.restore_manual == {"fan": True, "lighting": False}
    assert expired.state.urgency_until is None


def test_urgency_expiry_with_unknown_occupancy_restores_prior_auto_outputs() -> None:
    devices = {
        "fan": DevicePolicyState(on=True, mode="auto"),
        "lighting": DevicePolicyState(on=False, mode="auto"),
        "air_conditioner": DevicePolicyState(on=True, mode="auto"),
    }
    active = begin_urgency(AutomationState(), devices, NOW)
    expired = evaluate_automation(
        active,
        _inputs(
            now=NOW + URGENCY_DURATION,
            count=None,
            occupancy_fresh=False,
        ),
    )

    assert expired.urgency_status == "completed_camera_unknown"
    assert expired.targets == {
        "fan": True,
        "lighting": False,
        "air_conditioner": True,
    }


@pytest.mark.parametrize(("count", "fresh"), [(0, True), (1, True), (5, True), (None, False)])
def test_active_urgency_remains_above_all_main_flow_rules(
    count: int | None,
    fresh: bool,
) -> None:
    devices = {
        "fan": DevicePolicyState(on=False, mode="manual"),
        "lighting": DevicePolicyState(on=False, mode="manual"),
        "air_conditioner": DevicePolicyState(on=False, mode="auto"),
    }
    state = begin_urgency(AutomationState(), devices, NOW)
    decision = evaluate_automation(
        state,
        _inputs(
            now=NOW + URGENCY_DURATION - timedelta(milliseconds=1),
            count=count,
            occupancy_fresh=fresh,
            environment_fresh=False,
            temperature=None,
            humidity=None,
            ldr=4095,
            pir=True,
            states=devices,
        ),
    )

    assert decision.targets == {name: True for name in DEVICE_NAMES}
    assert decision.sources == {name: "urgency" for name in DEVICE_NAMES}
    assert decision.urgency_status == "monitoring"
    assert decision.state.urgency_until == NOW + URGENCY_DURATION
    assert decision.state.urgency_restore_states == devices


@pytest.mark.parametrize(("count", "fresh"), [(1, True), (5, True), (None, False)])
def test_urgency_expiry_restoration_is_not_overwritten_by_main_flow_on_same_tick(
    count: int | None,
    fresh: bool,
) -> None:
    devices = {
        "fan": DevicePolicyState(on=False, mode="auto"),
        "lighting": DevicePolicyState(on=True, mode="manual"),
        "air_conditioner": DevicePolicyState(on=True, mode="auto"),
    }
    state = begin_urgency(
        AutomationState(qualifying_since=NOW - timedelta(minutes=1)),
        devices,
        NOW,
    )
    expired = evaluate_automation(
        state,
        _inputs(
            now=NOW + URGENCY_DURATION,
            count=count,
            occupancy_fresh=fresh,
            ldr=501,
            temperature=24.0,
            humidity=50.0,
            states=devices,
        ),
    )

    assert expired.targets == {name: prior.on for name, prior in devices.items()}
    assert expired.sources == {
        "fan": "urgency_restore",
        "lighting": "manual_restore",
        "air_conditioner": "urgency_restore",
    }
    assert expired.restore_manual == {"lighting": True}
    assert expired.state.urgency_until is None
    assert expired.state.urgency_restore_states is None


def test_main_flow_resumes_after_urgency_expiry_restoration_tick() -> None:
    devices = {name: DevicePolicyState(on=False, mode="auto") for name in DEVICE_NAMES}
    state = begin_urgency(AutomationState(), devices, NOW)
    expired = evaluate_automation(
        state,
        _inputs(now=NOW + URGENCY_DURATION, count=1, ldr=50),
    )
    resumed = evaluate_automation(
        expired.state,
        _inputs(now=NOW + URGENCY_DURATION + timedelta(seconds=1), count=1, ldr=50),
    )

    assert expired.targets == {name: False for name in DEVICE_NAMES}
    assert resumed.urgency_status == "inactive"
    assert resumed.targets == {"fan": True, "lighting": False, "air_conditioner": False}


def test_ac_main_timer_does_not_accumulate_while_urgency_controls_outputs() -> None:
    devices = {name: DevicePolicyState(on=False, mode="auto") for name in DEVICE_NAMES}
    state = begin_urgency(
        AutomationState(qualifying_since=NOW - timedelta(seconds=31)),
        devices,
        NOW,
    )
    active = evaluate_automation(
        state,
        _inputs(now=NOW + timedelta(seconds=1), count=5),
    )
    expired = evaluate_automation(
        active.state,
        _inputs(now=NOW + URGENCY_DURATION, count=5),
    )
    resumed_at = NOW + URGENCY_DURATION + timedelta(seconds=1)
    resumed = evaluate_automation(expired.state, _inputs(now=resumed_at, count=5))
    before_boundary = evaluate_automation(
        resumed.state,
        _inputs(now=resumed_at + AC_DELAY - timedelta(milliseconds=1), count=5),
    )
    at_boundary = evaluate_automation(
        before_boundary.state,
        _inputs(now=resumed_at + AC_DELAY, count=5),
    )

    assert active.targets == {name: True for name in DEVICE_NAMES}
    assert active.state.qualifying_since is None
    assert expired.state.qualifying_since is None
    assert resumed.state.qualifying_since == resumed_at
    assert resumed.targets["air_conditioner"] is False
    assert before_boundary.targets["air_conditioner"] is False
    assert at_boundary.targets["air_conditioner"] is True


def test_urgency_expiry_fresh_zero_keeps_existing_all_off_behavior_even_for_manual_devices() -> (
    None
):
    devices = {name: DevicePolicyState(on=True, mode="manual") for name in DEVICE_NAMES}
    state = begin_urgency(AutomationState(), devices, NOW)
    expired = evaluate_automation(
        state,
        _inputs(now=NOW + URGENCY_DURATION, count=0, pir=True, states=devices),
    )

    assert expired.targets == {name: False for name in DEVICE_NAMES}
    assert expired.sources == {name: "urgency_timeout" for name in DEVICE_NAMES}
    assert expired.urgency_status == "completed_no_presence"
    assert expired.restore_manual == {}
