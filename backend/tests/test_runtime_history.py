"""Observer persistence behavior with an isolated transaction/result adapter.

These tests simulate storage results; they do not verify a physical ESP32 or
PostgreSQL deployment. Runtime arithmetic is tested independently.
"""

import asyncio
from datetime import UTC, date, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from app.domain.local_hardware import LIGHT_DEVICE_ID, LOCAL_CLASSROOM_ID
from app.domain.runtime import Segment, period_bounds, reconstruct, runtime_buckets, summarize
from app.repositories.runtime_history import load_runtime_history, store_runtime_observation
from app.schemas.mqtt import DeviceStatePayload

NOW = datetime(2026, 10, 26, 6, 0, tzinfo=UTC)


def _result(row: Any = None, rows: Any = None) -> MagicMock:
    result = MagicMock()
    result.mappings.return_value = result
    result.one_or_none.return_value = row
    result.all.return_value = rows or []
    return result


def _session(previous: dict[str, Any] | None = None) -> MagicMock:
    session = MagicMock()
    session.begin.return_value.__aenter__ = AsyncMock()
    session.begin.return_value.__aexit__ = AsyncMock(return_value=False)
    session.execute = AsyncMock(side_effect=[_result(), _result(previous), _result()])
    return session


def _previous(**changes: Any) -> dict[str, Any]:
    return {
        "id": uuid4(),
        "state": "on",
        "mode": "auto",
        "quality": [],
        "freshness_limit_seconds": 15,
        "last_observed_at": NOW - timedelta(seconds=2),
        "first_message_id": uuid4(),
        "last_message_id": uuid4(),
        **changes,
    }


async def _store(session: MagicMock, **changes: Any) -> bool:
    arguments = {
        "classroom_id": LOCAL_CLASSROOM_ID,
        "message_id": uuid4(),
        "occurred_at": NOW,
        "payload": DeviceStatePayload(
            device_id=LIGHT_DEVICE_ID,
            state={"on": True, "mode": "auto"},
            state_version=1,
        ),
        "retained": False,
        "now": NOW,
        **changes,
    }
    return await store_runtime_observation(session, **arguments)


@pytest.mark.parametrize(
    "changes",
    [
        {"retained": True},
        {"occurred_at": NOW - timedelta(seconds=16)},
        {"occurred_at": NOW + timedelta(seconds=6)},
    ],
)
async def test_retained_backlogged_and_future_messages_never_create_coverage(changes):
    session = _session()
    assert await _store(session, **changes) is False
    session.execute.assert_not_called()


async def test_first_reliable_observation_starts_history_without_backfill():
    session = _session()
    assert await _store(session)
    statement, parameters = session.execute.await_args_list[-1].args
    assert "INSERT INTO analytics_runtime_segments" in str(statement)
    assert parameters["occurred_at"] == NOW
    assert parameters["state"] == "on"
    assert parameters["mode"] == "auto"
    assert parameters["quality"] == []
    assert parameters["freshness_limit_seconds"] == 15
    assert parameters["device_id"] == LIGHT_DEVICE_ID


async def test_repeated_state_extends_durable_segment_after_process_restart():
    previous = _previous()
    session = _session(previous)
    assert await _store(session)
    statement, parameters = session.execute.await_args_list[-1].args
    assert "UPDATE analytics_runtime_segments" in str(statement)
    assert parameters["id"] == previous["id"]
    assert parameters["occurred_at"] == NOW
    assert "first_observed_at" not in str(statement)


@pytest.mark.parametrize("gap", [16, 3600])
async def test_restart_gap_splits_segment_instead_of_inventing_continuity(gap):
    session = _session(_previous(last_observed_at=NOW - timedelta(seconds=gap)))
    assert await _store(session)
    statement, parameters = session.execute.await_args_list[-1].args
    assert "INSERT INTO analytics_runtime_segments" in str(statement)
    assert parameters["occurred_at"] == NOW


async def test_transition_and_mode_change_create_distinct_evidence():
    for previous in (_previous(state="off"), _previous(mode="manual")):
        session = _session(previous)
        assert await _store(session)
        assert "INSERT INTO analytics_runtime_segments" in str(
            session.execute.await_args_list[-1].args[0]
        )


async def test_faults_are_unknown_and_recovery_cannot_extend_faulted_interval():
    session = _session(_previous())
    assert await _store(
        session,
        payload=DeviceStatePayload(
            device_id=LIGHT_DEVICE_ID,
            state={"on": True, "mode": "auto"},
            state_version=2,
            fault_codes=["relay_fault"],
        ),
    )
    parameters = session.execute.await_args_list[-1].args[1]
    assert parameters["state"] == "unknown"
    assert parameters["quality"] == ["firmware_fault"]
    recovered = _session(_previous(state="unknown", quality=["firmware_fault"]))
    assert await _store(recovered)
    assert "INSERT INTO analytics_runtime_segments" in str(
        recovered.execute.await_args_list[-1].args[0]
    )


@pytest.mark.parametrize("offset", [0, 1, 5])
async def test_duplicate_and_out_of_order_timestamp_is_ignored(offset):
    session = _session(_previous(last_observed_at=NOW + timedelta(seconds=offset)))
    assert await _store(session) is False
    assert session.execute.await_count == 2


async def test_replayed_message_id_cannot_extend_coverage_with_a_changed_timestamp():
    previous = _previous()
    session = _session(previous)
    assert await _store(session, message_id=previous["last_message_id"]) is False
    assert session.execute.await_count == 2


async def test_unregistered_logical_device_is_not_tracked():
    session = _session()
    assert await _store(
        session,
        payload=DeviceStatePayload(
            device_id=uuid4(), state={"on": True, "mode": "auto"}, state_version=0
        ),
    ) is False
    session.execute.assert_not_called()


async def test_history_reads_timezone_tracking_start_and_preceding_ttl_tail():
    session = MagicMock()
    tracking_start = NOW - timedelta(days=2)
    segment = {"device_id": LIGHT_DEVICE_ID, "state": "off"}
    session.execute = AsyncMock(
        side_effect=[
            _result({"timezone": "Asia/Kuala_Lumpur", "tracking_started_at": tracking_start}),
            _result(rows=[{"device_id": LIGHT_DEVICE_ID, "tracking_started_at": tracking_start}]),
            _result(rows=[segment]),
        ]
    )
    history = await load_runtime_history(
        session, LOCAL_CLASSROOM_ID, NOW, NOW + timedelta(hours=1), 15
    )
    assert history.classroom_exists
    assert history.timezone == "Asia/Kuala_Lumpur"
    assert history.tracking_started_at == tracking_start
    assert history.tracking_starts_by_device == {LIGHT_DEVICE_ID: tracking_start}
    assert history.segments == [segment]
    parameters = session.execute.await_args_list[-1].args[1]
    assert parameters["start"] == NOW - timedelta(seconds=300)
    assert LIGHT_DEVICE_ID in parameters["device_ids"]


async def test_new_classroom_has_no_fabricated_history():
    session = MagicMock()
    session.execute = AsyncMock(
        side_effect=[
            _result({"timezone": "Asia/Kuala_Lumpur", "tracking_started_at": None}),
            _result(rows=[]),
            _result(rows=[]),
        ]
    )
    history = await load_runtime_history(
        session, LOCAL_CLASSROOM_ID, NOW, NOW + timedelta(hours=1)
    )
    assert history.classroom_exists
    assert history.tracking_started_at is None
    assert history.segments == []
    assert history.tracking_starts_by_device == {}


async def test_device_tracking_start_is_available_outside_requested_report_period():
    tracking_start = NOW - timedelta(days=20)
    session = MagicMock()
    session.execute = AsyncMock(
        side_effect=[
            _result({"timezone": "Asia/Kuala_Lumpur", "tracking_started_at": tracking_start}),
            _result(rows=[{"device_id": LIGHT_DEVICE_ID, "tracking_started_at": tracking_start}]),
            _result(rows=[]),
        ]
    )
    history = await load_runtime_history(
        session, LOCAL_CLASSROOM_ID, NOW, NOW + timedelta(hours=1)
    )
    assert history.segments == []
    assert history.tracking_starts_by_device == {LIGHT_DEVICE_ID: tracking_start}
    statement = str(session.execute.await_args_list[1].args[0])
    assert ":start" not in statement
    assert ":end" not in statement


async def test_missing_classroom_is_distinguished_from_empty_history():
    session = MagicMock()
    session.execute = AsyncMock(return_value=_result())
    history = await load_runtime_history(
        session, LOCAL_CLASSROOM_ID, NOW, NOW + timedelta(hours=1)
    )
    assert not history.classroom_exists
    assert session.execute.await_count == 1


async def test_freshness_setting_change_starts_new_segment():
    previous = _previous(freshness_limit_seconds=15)
    session = _session(previous)
    assert await _store(session, stale_after_seconds=30)
    statement, parameters = session.execute.await_args_list[-1].args
    assert "INSERT INTO analytics_runtime_segments" in str(statement)
    assert parameters["freshness_limit_seconds"] == 30


@pytest.mark.parametrize("limit", [1, 301])
async def test_invalid_freshness_limits_are_rejected_before_storage(limit):
    session = _session()
    with pytest.raises(ValueError, match="between 2 and 300"):
        await _store(session, stale_after_seconds=limit)
    session.execute.assert_not_called()


async def test_unavailable_analytics_storage_is_bounded_and_session_is_closed(monkeypatch):
    from app import main

    session = MagicMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    sessions = MagicMock(return_value=session)
    cancelled = False

    async def hanging_write(*_args, **_kwargs):
        nonlocal cancelled
        try:
            await asyncio.Future()
        finally:
            cancelled = True

    monkeypatch.setattr(main, "store_runtime_observation", hanging_write)
    envelope = MagicMock()
    envelope.payload = MagicMock()
    # wait_for adds an independent deadline so a missing internal timeout fails
    # promptly rather than leaving an indefinitely hanging test.
    await asyncio.wait_for(
        main._store_runtime_observation_safely(
            sessions, envelope, False, 15, timeout_seconds=0.01
        ),
        timeout=1,
    )
    assert cancelled
    session.__aexit__.assert_awaited_once()


async def test_analytics_database_failure_does_not_escape_to_ingestion(monkeypatch):
    from sqlalchemy.exc import SQLAlchemyError

    from app import main

    session = MagicMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    sessions = MagicMock(return_value=session)
    monkeypatch.setattr(
        main, "store_runtime_observation", AsyncMock(side_effect=SQLAlchemyError("unavailable"))
    )
    await main._store_runtime_observation_safely(sessions, MagicMock(), False, 15)
    session.__aexit__.assert_awaited_once()


def test_activation_on_hour_boundary_belongs_to_the_starting_hour():
    end = NOW + timedelta(hours=2)
    boundary = NOW + timedelta(hours=1)
    intervals = reconstruct(
        [
            Segment("off", NOW, boundary - timedelta(seconds=2)),
            Segment("on", boundary, end),
        ],
        NOW,
        end,
    )
    total = summarize(intervals, NOW, end)
    buckets = runtime_buckets(intervals, NOW, end, "Asia/Kuala_Lumpur", True)
    assert total["activations"] == 1
    assert [bucket["activations"] for bucket in buckets] == [0, 1]
    assert sum(bucket["transitions"] for bucket in buckets) == total["transitions"]


def test_midnight_activation_belongs_to_new_calendar_day():
    midnight, _ = period_bounds(date(2026, 10, 27), "day", "Asia/Kuala_Lumpur")
    start, end = midnight - timedelta(minutes=1), midnight + timedelta(minutes=1)
    intervals = reconstruct(
        [
            Segment("off", start, midnight - timedelta(seconds=2)),
            Segment("on", midnight, end),
        ],
        start,
        end,
    )
    days = runtime_buckets(intervals, start, end, "Asia/Kuala_Lumpur", False)
    assert [bucket["on_seconds"] for bucket in days] == [0, 60]
    assert [bucket["activations"] for bucket in days] == [0, 1]
