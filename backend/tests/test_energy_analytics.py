from datetime import UTC, date, datetime, timedelta

import pytest

from app.core.config import Settings
from app.domain.local_hardware import LIGHT_DEVICE_ID, LOCAL_CLASSROOM_ID, LOCAL_RELAY_DEVICES
from app.domain.runtime import period_bounds
from app.repositories.runtime_history import RuntimeHistory
from app.services.energy_analytics import build_analytics_report, calculate_energy

START, END = period_bounds(date(2026, 10, 25), "day", "Asia/Kuala_Lumpur")
POWERS = {"lighting": 10, "fan": 60, "air_conditioner": 1000}


def build(rows=None, now=END, powers=None):
    history = RuntimeHistory(True, START if rows else None, rows or [], "Asia/Kuala_Lumpur")
    return build_analytics_report(
        history=history,
        classroom_id=LOCAL_CLASSROOM_ID,
        start=START,
        end=END,
        now=now,
        period="day",
        powers=powers or POWERS,
    )


def row(key, state="on", first=START, last=END):
    return {
        "device_id": LOCAL_RELAY_DEVICES[key]["id"],
        "state": state,
        "first_observed_at": first,
        "last_observed_at": last,
        "mode": "auto",
        "quality": [],
    }


def test_example_baseline_and_independent_energy_formula():
    result = build([row(key) for key in POWERS])
    assert [d.energy.daily_24h_baseline_kwh for d in result.devices] == [0.24, 1.44, 24]
    assert result.total_energy.daily_24h_baseline_kwh == pytest.approx(25.68)
    assert result.total_energy.estimated_energy_kwh == pytest.approx(25.68)
    assert result.total_energy.final_savings_pct == 0
    assert not result.is_partial
    for watts in POWERS.values():
        energy = calculate_energy(watts, 2 * 3600, 24 * 3600, 0, 1, True)
        assert energy.estimated_energy_kwh == watts * 2 / 1000


def test_configurable_power_changes_estimate_and_baseline_only():
    first = build([row("lighting")])
    second = build([row("lighting")], powers={**POWERS, "lighting": 20})
    assert (
        second.devices[0].energy.estimated_energy_kwh
        == 2 * first.devices[0].energy.estimated_energy_kwh
    )
    assert second.devices[0].runtime == first.devices[0].runtime
    assert second.devices[0].device_id == LIGHT_DEVICE_ID
    settings = Settings(database_url="postgresql+asyncpg://unused", analytics_light_power_w=20)
    assert settings.analytics_light_power_w == 20


def test_partial_day_cannot_claim_final_savings_and_future_is_not_unknown():
    now = START + timedelta(hours=6)
    result = build([row(key, last=now) for key in POWERS], now=now)
    assert result.is_partial
    assert result.period_elapsed_seconds == 6 * 3600
    assert result.total_energy.estimated_energy_kwh == pytest.approx(25.68 / 4)
    assert result.total_energy.period_baseline_kwh == pytest.approx(25.68)
    assert result.total_energy.provisional_difference_pct == pytest.approx(75)
    assert result.total_energy.final_savings_pct is None
    assert all(d.runtime.unknown_seconds == 0 for d in result.devices)


def test_no_history_is_unknown_and_never_a_complete_savings_report():
    result = build()
    assert result.total_energy.estimated_energy_kwh == 0
    assert result.total_energy.final_savings_pct is None
    assert all(d.runtime.unknown_seconds == 86400 for d in result.devices)
    assert any("no_history" in warning for warning in result.warnings)


def test_gap_energy_excludes_unknown_and_does_not_count_it_as_off():
    result = build([row("lighting", last=START + timedelta(hours=1))])
    light = result.devices[0]
    assert light.runtime.on_seconds == 3615
    assert light.runtime.off_seconds == 0
    assert light.energy.estimated_energy_kwh == pytest.approx(10 * (3615 / 3600) / 1000)
    assert light.energy.final_savings_pct is None


def test_zero_power_has_no_percentage():
    result = calculate_energy(0, 0, 86400, 0, 1, True)
    assert result.provisional_difference_pct is None
    assert result.final_savings_pct is None


@pytest.mark.parametrize(
    "watts,on,elapsed,unknown,days",
    [
        (-1, 0, 86400, 0, 1),
        (10, -1, 86400, 0, 1),
        (10, 100, 99, 0, 1),
        (10, 5, 10, 6, 1),
        (float("nan"), 0, 86400, 0, 1),
        (10, 0, 86400, 0, 0),
    ],
)
def test_invalid_and_inconsistent_inputs_are_rejected(watts, on, elapsed, unknown, days):
    with pytest.raises(ValueError):
        calculate_energy(watts, on, elapsed, unknown, days, True)


def test_negative_difference_is_preserved_for_25_hour_dst_day():
    result = calculate_energy(1000, 25 * 3600, 25 * 3600, 0, 1, True)
    assert result.theoretical_difference_kwh == -1
    assert result.final_savings_pct == pytest.approx(-100 / 24)


def test_combined_estimate_sums_devices_and_serializes_without_ai_key():
    result = build([row("lighting"), row("fan", "off")])
    assert result.total_energy.estimated_energy_kwh == pytest.approx(0.24)
    assert "api_key" not in result.model_dump_json()
    assert result.devices[2].runtime.unknown_seconds == 86400
    assert result.devices[0].hourly[0].on_seconds == 3600


def test_period_baseline_scales_by_calendar_days():
    assert calculate_energy(10, 0, 0, 0, 7, False).period_baseline_kwh == pytest.approx(1.68)
    assert calculate_energy(10, 0, 0, 0, 31, False).period_baseline_kwh == pytest.approx(7.44)


def test_future_period_has_no_elapsed_or_fabricated_unknown_time():
    result = build(now=datetime(2020, 1, 1, tzinfo=UTC))
    assert result.period_elapsed_seconds == 0
    assert all(d.runtime.on_seconds == d.runtime.unknown_seconds == 0 for d in result.devices)


def test_historical_observation_timestamp_is_preserved_across_period_boundary():
    later = END + timedelta(hours=3)
    result = build([row("lighting", last=later)])
    assert result.devices[0].last_observed_at == later
    assert result.devices[0].runtime.on_seconds == 86400


def test_recorded_segment_freshness_survives_new_reporting_policy():
    segment = {**row("lighting", last=START), "freshness_limit_seconds": 30}
    result = build([segment])
    assert result.devices[0].runtime.on_seconds == 30
    assert any("recorded freshness" in warning for warning in result.warnings)
