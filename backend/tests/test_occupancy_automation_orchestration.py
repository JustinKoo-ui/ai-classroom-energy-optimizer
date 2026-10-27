from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from app.services import occupancy_automation as automation

NOW = datetime(2026, 10, 26, 8, 0, tzinfo=UTC)


def _state(**overrides: Any) -> dict[str, Any]:
    return {
        "qualifying_since": None,
        "below_threshold_since": None,
        "pir_scan_until": None,
        "fallback_lights_latched": False,
        "last_motion_detected": False,
        "camera_was_fresh": False,
        "urgency_started_at": None,
        "urgency_until": None,
        "urgency_result": "inactive",
        "urgency_restore_states": {},
        "updated_at": NOW,
        **overrides,
    }


def _device(name: str, **overrides: Any) -> dict[str, Any]:
    return {
        "id": automation.DEVICE_IDS[name],
        "enabled": True,
        "capabilities": {"supported_actions": ["set_power", "set_mode"]},
        "operational_status": "online",
        "reported_state": {"on": False, "mode": "auto"},
        "fault_codes": [],
        "last_seen_at": NOW,
        "pending_command_id": None,
        "previous_applied_at": None,
        **overrides,
    }


async def _evaluate(
    monkeypatch: pytest.MonkeyPatch,
    *,
    state: dict[str, Any] | None = None,
    count: int = 3,
    qualification_interrupted: bool = False,
    occupancy_age: timedelta = timedelta(0),
    environment_age: timedelta = timedelta(0),
    temperature: float = 27.0,
    humidity: float = 50.0,
    ldr: int = 1001,
    pir: bool = True,
    devices: list[dict[str, Any]] | None = None,
) -> tuple[int, AsyncMock, dict[str, Any]]:
    """Run the real policy/service against isolated database-result snapshots."""
    occupancy = {
        "person_count": count,
        "observed_at": NOW - occupancy_age,
        "qualification_interrupted": qualification_interrupted,
    }
    environment = {
        "temperature_c": temperature,
        "relative_humidity_pct": humidity,
        "illuminance_adc": ldr,
        "motion_detected": pir,
        "sample_interval_ms": 2000,
        "occurred_at": NOW - environment_age,
    }

    def result(*, row: Any = None, rows: Any = None, scalar: Any = None) -> MagicMock:
        value = MagicMock()
        value.mappings.return_value = value
        value.one.return_value = row
        value.one_or_none.return_value = row
        value.all.return_value = rows
        value.scalar_one_or_none.return_value = scalar
        return value

    session = MagicMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    session.begin.return_value.__aenter__ = AsyncMock()
    session.begin.return_value.__aexit__ = AsyncMock(return_value=False)
    session.execute = AsyncMock(
        side_effect=[
            result(),
            result(),
            result(row=state or _state()),
            result(scalar=uuid4()),
            result(row=occupancy),
            result(row=environment),
            result(rows=devices or [_device(name) for name in automation.DEVICE_NAMES]),
            result(),
        ]
    )
    persist = AsyncMock()
    monkeypatch.setattr(automation, "_persist_allowed_command", persist)

    queued = await automation.evaluate_local_automation(MagicMock(return_value=session), now=NOW)
    # The historical interruption query must use the same three-person gate.
    assert session.execute.await_args_list[4].args[1]["qualifying_count"] == 3
    stored_state = session.execute.await_args_list[-1].args[1]
    return queued, persist, stored_state


async def test_qualified_main_targets_use_existing_outbox_and_reported_modes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    queued, persist, stored = await _evaluate(
        monkeypatch,
        state=_state(qualifying_since=NOW - timedelta(seconds=31)),
    )

    assert queued == 3
    commands = {call.kwargs["device_id"]: call.kwargs for call in persist.await_args_list}
    assert set(commands) == set(automation.DEVICE_IDS.values())
    for command in commands.values():
        assert command["requested_value"] == {
            "on": True,
            "mode": "auto",
            "source": "automation",
        }
        assert command["trigger"] == "occupancy"
        assert command["expires_at"] == NOW + automation.COMMAND_LIFETIME
        assert command["input_snapshot"]["occupancy_count"] == 3
        assert command["input_snapshot"]["environment_fresh"] is True
    assert stored["qualifying_since"] == NOW - timedelta(seconds=31)


@pytest.mark.parametrize(
    ("count", "temperature", "humidity", "expected"),
    [
        (2, 80.0, 100.0, False),
        (3, 26.0, 65.0, True),
        (3, 26.0, 50.0, True),
        (3, 24.0, 65.0, True),
        (3, 25.9, 64.9, False),
        (3, 26.1, 50.0, True),
        (3, 24.0, 65.1, True),
        (4, 27.0, 66.0, True),
    ],
)
async def test_three_person_ac_gate_applies_before_either_environment_threshold(
    monkeypatch: pytest.MonkeyPatch,
    count: int,
    temperature: float,
    humidity: float,
    expected: bool,
) -> None:
    _, persist, stored = await _evaluate(
        monkeypatch,
        state=_state(qualifying_since=NOW - timedelta(seconds=31)),
        count=count,
        temperature=temperature,
        humidity=humidity,
    )

    ac_commands = [
        call.kwargs for call in persist.await_args_list
        if call.kwargs["device_id"] == automation.AC_DEVICE_ID
    ]
    assert len(ac_commands) == int(expected)
    if expected:
        assert ac_commands[0]["requested_value"] == {
            "on": True, "mode": "auto", "source": "automation",
        }
        assert ac_commands[0]["input_snapshot"]["occupancy_count"] == count
    if count < 3:
        assert stored["qualifying_since"] is None


async def test_drop_from_three_to_two_turns_automatic_ac_off_and_resets_timer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, persist, stored = await _evaluate(
        monkeypatch,
        state=_state(qualifying_since=NOW - timedelta(seconds=31)),
        count=2,
        temperature=80.0,
        humidity=100.0,
        devices=[
            _device(name, reported_state={"on": True, "mode": "auto"})
            for name in automation.DEVICE_NAMES
        ],
    )

    ac_commands = [
        call.kwargs for call in persist.await_args_list
        if call.kwargs["device_id"] == automation.AC_DEVICE_ID
    ]
    assert len(ac_commands) == 1
    assert ac_commands[0]["requested_value"] == {
        "on": False, "mode": "auto", "source": "automation",
    }
    assert stored["qualifying_since"] is None


@pytest.mark.parametrize(
    ("elapsed", "expected"),
    [(timedelta(seconds=4), False), (timedelta(seconds=4, milliseconds=999), False),
     (timedelta(seconds=5), True), (timedelta(seconds=5, milliseconds=1), True)],
)
async def test_automatic_ac_command_uses_five_second_qualification(
    monkeypatch: pytest.MonkeyPatch,
    elapsed: timedelta,
    expected: bool,
) -> None:
    _, persist, stored = await _evaluate(
        monkeypatch,
        state=_state(qualifying_since=NOW - elapsed),
        count=3,
    )

    ac_commands = [
        call.kwargs for call in persist.await_args_list
        if call.kwargs["device_id"] == automation.AC_DEVICE_ID
    ]
    assert len(ac_commands) == int(expected)
    assert stored["qualifying_since"] == NOW - elapsed
    if expected:
        assert ac_commands[0]["requested_value"]["on"] is True


@pytest.mark.parametrize(
    ("ldr", "pir", "expected"),
    [
        (50, True, False),
        (861, True, False),
        (999, True, False),
        (1000, True, False),
        (1001, True, True),
        (1001, False, False),
        (4095, False, False),
        (4095, True, True),
    ],
)
async def test_lighting_requires_both_fresh_inputs_without_occupancy(
    monkeypatch: pytest.MonkeyPatch,
    ldr: int,
    pir: bool,
    expected: bool,
) -> None:
    queued, persist, stored = await _evaluate(
        monkeypatch,
        count=0,
        ldr=ldr,
        pir=pir,
    )

    assert queued == int(expected)
    assert stored["qualifying_since"] is None
    if not expected:
        persist.assert_not_awaited()
        return
    persist.assert_awaited_once()
    command = persist.await_args.kwargs
    assert command["device_id"] == automation.LIGHT_DEVICE_ID
    assert command["requested_value"] == {
        "on": True,
        "mode": "auto",
        "source": "pir_fallback",
    }
    assert command["input_snapshot"]["occupancy_count"] == 0
    assert command["input_snapshot"]["motion_detected"] is pir


@pytest.mark.parametrize(
    ("ldr", "pir"),
    [(4095, False), (1001, False), (1000, True), (999, True), (861, True)],
)
async def test_missing_motion_or_adc_not_above_threshold_turns_off_reported_auto_light(
    monkeypatch: pytest.MonkeyPatch,
    ldr: int,
    pir: bool,
) -> None:
    _, persist, _ = await _evaluate(
        monkeypatch,
        count=0,
        ldr=ldr,
        pir=pir,
        devices=[
            _device(name, reported_state={"on": True, "mode": "auto"})
            for name in automation.DEVICE_NAMES
        ],
    )

    light_commands = [
        call.kwargs
        for call in persist.await_args_list
        if call.kwargs["device_id"] == automation.LIGHT_DEVICE_ID
    ]
    assert len(light_commands) == 1
    assert light_commands[0]["requested_value"]["on"] is False
    assert light_commands[0]["requested_value"]["mode"] == "auto"


@pytest.mark.parametrize("evaluation_age", [timedelta(seconds=16), timedelta(seconds=-1)])
async def test_missing_or_future_evaluation_continuity_restarts_ac_timer(
    monkeypatch: pytest.MonkeyPatch,
    evaluation_age: timedelta,
) -> None:
    queued, persist, stored = await _evaluate(
        monkeypatch,
        state=_state(
            qualifying_since=NOW - timedelta(seconds=31),
            updated_at=NOW - evaluation_age,
        ),
    )

    assert queued == 2
    assert stored["qualifying_since"] == NOW
    assert all(
        call.kwargs["device_id"] != automation.AC_DEVICE_ID
        for call in persist.await_args_list
    )


async def test_below_threshold_observation_between_evaluations_restarts_ac_timer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    queued, persist, stored = await _evaluate(
        monkeypatch,
        state=_state(qualifying_since=NOW - timedelta(seconds=31)),
        count=3,
        qualification_interrupted=True,
    )

    assert queued == 2
    assert stored["qualifying_since"] == NOW
    assert all(
        call.kwargs["device_id"] != automation.AC_DEVICE_ID
        for call in persist.await_args_list
    )


async def test_normal_automation_does_not_override_manual_relays(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    queued, persist, _ = await _evaluate(
        monkeypatch,
        state=_state(qualifying_since=NOW - timedelta(seconds=31)),
        devices=[
            _device(name, reported_state={"on": False, "mode": "manual"})
            for name in automation.DEVICE_NAMES
        ],
    )

    assert queued == 0
    persist.assert_not_awaited()


@pytest.mark.parametrize(
    "guard",
    [
        {"enabled": False},
        {"operational_status": "offline"},
        {"last_seen_at": NOW - timedelta(seconds=16)},
        {"last_seen_at": NOW + timedelta(seconds=1)},
        {"fault_codes": ["relay_fault"]},
        {"capabilities": {"supported_actions": ["set_mode"]}},
        {"pending_command_id": uuid4()},
        {"previous_applied_at": NOW - timedelta(seconds=1)},
    ],
)
@pytest.mark.parametrize("urgency_active", [False, True])
async def test_main_targets_do_not_bypass_device_delivery_guards(
    monkeypatch: pytest.MonkeyPatch,
    guard: dict[str, Any],
    urgency_active: bool,
) -> None:
    queued, persist, _ = await _evaluate(
        monkeypatch,
        state=_state(
            qualifying_since=NOW - timedelta(seconds=31),
            urgency_started_at=NOW - timedelta(minutes=1) if urgency_active else None,
            urgency_until=NOW + timedelta(minutes=4) if urgency_active else None,
        ),
        devices=[
            _device(
                name,
                reported_state={"on": False, "mode": "manual" if urgency_active else "auto"},
                **guard,
            )
            for name in automation.DEVICE_NAMES
        ],
    )

    assert queued == 0
    persist.assert_not_awaited()


@pytest.mark.parametrize("invalid_input", ["occupancy", "environment"])
async def test_stale_inputs_cannot_keep_automatic_ac_on(
    monkeypatch: pytest.MonkeyPatch,
    invalid_input: str,
) -> None:
    queued, persist, stored = await _evaluate(
        monkeypatch,
        state=_state(qualifying_since=NOW - timedelta(seconds=31)),
        occupancy_age=timedelta(seconds=16) if invalid_input == "occupancy" else timedelta(0),
        environment_age=timedelta(seconds=7) if invalid_input == "environment" else timedelta(0),
        devices=[
            _device(name, reported_state={"on": True, "mode": "auto"})
            for name in automation.DEVICE_NAMES
        ],
    )

    ac_commands = [
        call.kwargs
        for call in persist.await_args_list
        if call.kwargs["device_id"] == automation.AC_DEVICE_ID
    ]
    assert queued >= 1
    assert len(ac_commands) == 1
    assert ac_commands[0]["requested_value"]["on"] is False
    assert ac_commands[0]["input_snapshot"][f"{invalid_input}_fresh"] is False
    if invalid_input == "occupancy":
        assert stored["qualifying_since"] is None
        assert ac_commands[0]["input_snapshot"]["occupancy_count"] is None


async def test_active_urgency_keeps_privileged_manual_command_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    queued, persist, stored = await _evaluate(
        monkeypatch,
        state=_state(
            urgency_started_at=NOW - timedelta(minutes=1),
            urgency_until=NOW + timedelta(minutes=4),
            urgency_result="monitoring",
        ),
        count=0,
        devices=[
            _device(name, reported_state={"on": False, "mode": "manual"})
            for name in automation.DEVICE_NAMES
        ],
    )

    assert queued == 3
    for call in persist.await_args_list:
        assert call.kwargs["requested_value"] == {
            "on": True,
            "mode": "manual",
            "source": "urgency",
        }
        assert call.kwargs["trigger"] == "override"
    assert stored["urgency_result"] == "monitoring"
    assert stored["urgency_until"] == NOW + timedelta(minutes=4)


async def test_urgency_expiry_restores_states_when_zero_count_has_become_stale(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    restore = {name: {"on": True, "mode": "manual"} for name in automation.DEVICE_NAMES}
    queued, persist, stored = await _evaluate(
        monkeypatch,
        state=_state(
            urgency_started_at=NOW - timedelta(minutes=5),
            urgency_until=NOW,
            urgency_result="monitoring",
            urgency_restore_states=restore,
        ),
        count=0,
        occupancy_age=timedelta(seconds=16),
        devices=[
            _device(name, reported_state={"on": False, "mode": "manual"})
            for name in automation.DEVICE_NAMES
        ],
    )

    assert queued == 3
    for call in persist.await_args_list:
        assert call.kwargs["requested_value"] == {
            "on": True,
            "mode": "manual",
            "source": "manual_restore",
        }
        assert call.kwargs["input_snapshot"]["occupancy_count"] is None
        assert call.kwargs["trigger"] == "override"
    assert stored["urgency_result"] == "completed_camera_unknown"
    assert stored["urgency_until"] is None


async def test_urgency_expiry_restores_prior_auto_outputs_before_main_flow_resumes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    restore = {name: {"on": True, "mode": "auto"} for name in automation.DEVICE_NAMES}
    queued, persist, stored = await _evaluate(
        monkeypatch,
        state=_state(
            urgency_started_at=NOW - timedelta(minutes=5),
            urgency_until=NOW,
            urgency_result="monitoring",
            urgency_restore_states=restore,
        ),
        count=1,
    )

    assert queued == 3
    for call in persist.await_args_list:
        assert call.kwargs["requested_value"] == {
            "on": True,
            "mode": "auto",
            "source": "urgency_restore",
        }
    assert stored["urgency_result"] == "completed_presence"
