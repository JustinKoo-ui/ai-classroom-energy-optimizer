from datetime import UTC, date, datetime, timedelta

import pytest

from app.domain.runtime import Segment, period_bounds, reconstruct, runtime_buckets, summarize

START = datetime(2026, 10, 25, 16, tzinfo=UTC)  # Malaysia midnight


def seg(state, first, last=None, mode="auto"):
    return Segment(
        state,
        START + timedelta(seconds=first),
        START + timedelta(seconds=first if last is None else last),
        mode,
    )


def report(segments, seconds=60):
    end = START + timedelta(seconds=seconds)
    intervals = reconstruct(segments, START, end)
    return summarize(intervals, START, end)


def test_known_observed_changes_and_repeated_state():
    result = report([seg("off", 0, 10), seg("on", 12, 30), seg("off", 32, 60)])
    assert result["on_seconds"] == 20
    assert result["off_seconds"] == 40
    assert result["activations"] == 1
    assert result["transitions"] == 2
    assert result["average_on_seconds"] == result["longest_on_seconds"] == 20


def test_gaps_and_restart_are_unknown_and_initial_on_is_not_activation():
    result = report([seg("on", 0, 10), seg("on", 50, 60)])
    assert result["on_seconds"] == 35
    assert result["unknown_seconds"] == 25
    assert result["off_seconds"] == 0
    assert result["activations"] == 0
    assert result["longest_on_seconds"] == 25


def test_explicit_unknown_missing_and_stale():
    assert report([])["unknown_seconds"] == 60
    result = report([seg("off", 0), seg("unknown", 10, 20)])
    assert result["unknown_seconds"] == 50
    assert result["off_seconds"] == 10


def test_midnight_and_reporting_timezone_allocation():
    start = START - timedelta(seconds=10)
    end = START + timedelta(seconds=20)
    intervals = reconstruct([seg("on", -10, 20)], start, end)
    days = runtime_buckets(intervals, start, end, "Asia/Kuala_Lumpur", False)
    assert [bucket["on_seconds"] for bucket in days] == [10, 20]
    assert days[1]["label"].startswith("2026-10-26T00:00")


def test_tracking_begins_at_first_record_without_fabricated_prior_history():
    result = report([seg("on", 30, 60)])
    assert result["unknown_seconds"] == 30
    assert result["on_seconds"] == 30
    assert result["coverage_pct"] == 50


def test_mode_change_does_not_split_on_run_or_count_transition():
    result = report([seg("on", 0, 10), seg("on", 12, 60, "manual")])
    assert result["on_seconds"] == result["longest_on_seconds"] == 60
    assert result["transitions"] == 0


def test_dst_hours_and_calendar_periods():
    first, last = period_bounds(date(2026, 11, 1), "day", "America/New_York")
    assert (last - first).total_seconds() == 25 * 3600
    buckets = runtime_buckets(reconstruct([], first, last), first, last, "America/New_York", True)
    assert len(buckets) == 25
    first, last = period_bounds(date(2026, 10, 28), "week", "Asia/Kuala_Lumpur")
    assert first == START
    assert (last - first).days == 7
    first, last = period_bounds(date(2026, 10, 28), "month", "Asia/Kuala_Lumpur")
    assert (last - first).days == 31


def test_invalid_durations_overlaps_and_naive_timestamps_are_rejected():
    with pytest.raises(ValueError):
        seg("on", 10, 0)
    with pytest.raises(ValueError):
        report([seg("on", 0, 20), seg("off", 10, 30)])
    with pytest.raises(ValueError):
        Segment("on", datetime(2026, 1, 1), datetime(2026, 1, 1))
    with pytest.raises(ValueError):
        summarize([], START, START + timedelta(seconds=1))


def test_hourly_buckets_preserve_total_runtime():
    end = START + timedelta(hours=2)
    intervals = reconstruct([seg("on", 0, 7200)], START, end)
    buckets = runtime_buckets(intervals, START, end, "Asia/Kuala_Lumpur", True)
    assert [bucket["on_seconds"] for bucket in buckets] == [3600, 3600]


def test_recorded_freshness_is_not_reinterpreted_after_configuration_changes():
    segment = Segment("on", START, START, "auto", 15)
    end = START + timedelta(seconds=60)
    for current_threshold in (5, 30):
        intervals = reconstruct([segment], START, end, current_threshold)
        assert summarize(intervals, START, end)["on_seconds"] == 15


def test_subsecond_durations_do_not_exceed_valid_percentage_bounds():
    from app.schemas.analytics import RuntimeMetrics

    end = START + timedelta(microseconds=300000)
    intervals = reconstruct([seg("off", 0, 0), seg("on", 0.1, 0.3)], START, end)
    metrics = RuntimeMetrics.model_validate(summarize(intervals, START, end))
    assert metrics.coverage_pct == 100
    assert metrics.on_seconds == 0.2
