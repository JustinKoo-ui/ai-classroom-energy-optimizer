from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.schemas.device_control import ManualCommandRequest
from app.services.device_control import _evaluate_policy


def _device(**overrides: object) -> SimpleNamespace:
    values: dict[str, object] = {
        "enabled": True,
        "operational_status": "online",
        "last_seen_at": datetime.now(UTC),
        "fault_codes": [],
        "reported_state": {"mode": "manual", "on": False},
        "capabilities": {"supported_actions": ["set_power", "set_mode"]},
        "pending_command_id": None,
        "previous_applied_at": None,
        "environment_at": datetime.now(UTC),
        "sample_interval_ms": 2000,
        "motion_detected": True,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_manual_power_on_is_allowed_only_with_fresh_motion_and_online_device() -> None:
    request = ManualCommandRequest(
        action="set_power",
        value={"on": True},
        reason="Local test",
    )

    reasons, _, _ = _evaluate_policy(device=_device(), request=request, now=datetime.now(UTC))

    assert reasons == []


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        ({"motion_detected": False}, "pir_master_no_motion"),
        ({"motion_detected": None}, "motion_state_unknown"),
        ({"environment_at": datetime.now(UTC) - timedelta(seconds=10)}, "safety_state_stale"),
        ({"operational_status": "offline"}, "device_offline"),
        ({"fault_codes": ["interlock_open"]}, "device_fault"),
    ],
)
def test_manual_power_on_is_denied_for_safety_conditions(
    overrides: dict[str, object],
    reason: str,
) -> None:
    request = ManualCommandRequest(
        action="set_power",
        value={"on": True},
        reason="Local test",
    )

    reasons, _, _ = _evaluate_policy(
        device=_device(**overrides), request=request, now=datetime.now(UTC)
    )

    assert reason in reasons


def test_manual_power_off_is_not_blocked_by_no_motion() -> None:
    request = ManualCommandRequest(
        action="set_power",
        value={"on": False},
        reason="Local test",
    )

    reasons, _, _ = _evaluate_policy(
        device=_device(motion_detected=False),
        request=request,
        now=datetime.now(UTC),
    )

    assert reasons == []


def test_power_command_is_denied_while_controller_is_in_auto_mode() -> None:
    request = ManualCommandRequest(
        action="set_power",
        value={"on": True},
        reason="Local test",
    )

    reasons, _, _ = _evaluate_policy(
        device=_device(reported_state={"mode": "auto", "on": True}),
        request=request,
        now=datetime.now(UTC),
    )

    assert "device_auto_mode" in reasons


def test_variable_actions_are_rejected_by_request_contract() -> None:
    with pytest.raises(ValidationError):
        ManualCommandRequest(
            action="set_power",
            value={"on": True, "brightness_pct": 50},
            reason="Unsupported control",
        )
