"""Regression tests for scoped solar ingestion and prototype display freshness."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import httpx
import paho.mqtt.client as mqtt
import pytest
from fastapi import FastAPI

from app.api.v1.routers import prototype_monitor
from app.core.config import Settings
from app.core.private_bridge import PROXY_TOKEN_HEADER, PrivateBridgeMiddleware
from app.domain.local_hardware import LOCAL_CLASSROOM_ID, LOCAL_SITE_ID
from app.integrations.mqtt.ingestion import MqttIngestion
from app.repositories.solar_monitor import read_prototype_snapshot, reading_freshness
from app.schemas.common import Freshness

PRODUCER_ID = "solar-tracker-esp32"
TOPIC = f"aceo/v1/{LOCAL_SITE_ID}/{LOCAL_CLASSROOM_ID}/solar/{PRODUCER_ID}"
ROUTE = f"/api/v1/classrooms/{LOCAL_CLASSROOM_ID}/prototype-monitor"


def settings(**overrides):
    return Settings(_env_file=None, database_url="postgresql+asyncpg://unused", **overrides)


def telemetry():
    return {
        "boot_id": str(uuid4()),
        "gps": {
            "fix": False,
            "time_valid": False,
            "latitude": None,
            "longitude": None,
            "last_fix_age_ms": None,
        },
        "tracking": {
            "mode": "auto",
            "target_azimuth": None,
            "actual_azimuth": None,
            "target_zenith": None,
            "actual_zenith": None,
            "azimuth_pwm": 0,
            "zenith_pwm": 0,
            "stop_reason": "waiting_for_gps",
        },
        "solar_power": {"voltage": None, "current": None, "power": None, "energy": None},
        "sensors": {
            "ina219_available": False,
            "azimuth_mpu_available": True,
            "zenith_mpu_available": True,
        },
    }


def envelope(payload):
    return {
        "schema_version": "1.0",
        "message_id": str(uuid4()),
        "occurred_at": None,
        "site_id": str(LOCAL_SITE_ID),
        "classroom_id": str(LOCAL_CLASSROOM_ID),
        "producer_id": PRODUCER_ID,
        "payload": payload,
    }


def mqtt_message(topic, body, *, retained=False):
    message = mqtt.MQTTMessage()
    message.topic = topic.encode()
    message.payload = json.dumps(body).encode()
    message.retain = retained
    return message


@pytest.fixture
def ingestion():
    return MqttIngestion(
        host="localhost",
        port=1883,
        topic_root="aceo/v1",
        client_id="test-solar-monitor",
        persist=AsyncMock(),
        persist_gateway_availability=AsyncMock(),
        solar_identity=(LOCAL_SITE_ID, LOCAL_CLASSROOM_ID, PRODUCER_ID),
        persist_solar_telemetry=AsyncMock(),
        persist_solar_status=AsyncMock(),
    )


async def test_solar_subscribes_only_to_registered_source_and_never_updates_relays(ingestion):
    body = envelope(telemetry())
    status = envelope({"boot_id": body["payload"]["boot_id"], "status": "offline"})

    await ingestion._handle_message(mqtt_message(f"{TOPIC}/telemetry", body))
    await ingestion._handle_message(mqtt_message(f"{TOPIC}/status", status, retained=True))

    assert [topic for topic in ingestion.topics if "/solar/" in topic] == [
        f"{TOPIC}/telemetry",
        f"{TOPIC}/status",
    ]
    ingestion._persist_solar_telemetry.assert_awaited_once()
    reading, _ = ingestion._persist_solar_telemetry.await_args.args
    assert reading.payload.gps.enabled is True
    assert reading.payload.tracking.yaw_sequence is None
    assert reading.payload.tracking.fixed_yaw is None
    ingestion._persist_solar_status.assert_awaited_once()
    ingestion._persist_gateway_availability.assert_not_awaited()


@pytest.mark.parametrize(
    ("stop_reason", "yaw_sequence"),
    [
        (
            None,
            {
                "kind": "run",
                "phase": "anticlockwise",
                "leg_progress_deg": 22.5,
                "leg_target_deg": 360,
                "net_rotation_deg": -2.5,
                "result": None,
            },
        ),
        (
            "local_hold",
            {
                "kind": "test",
                "phase": "idle",
                "leg_progress_deg": None,
                "leg_target_deg": None,
                "net_rotation_deg": 10,
                "result": "user_stopped",
            },
        ),
    ],
)
async def test_solar_tracking_records_local_yaw_state(ingestion, stop_reason, yaw_sequence):
    payload = telemetry()
    payload["tracking"].update(stop_reason=stop_reason, yaw_sequence=yaw_sequence)

    await ingestion._handle_message(mqtt_message(f"{TOPIC}/telemetry", envelope(payload)))

    reading, _ = ingestion._persist_solar_telemetry.await_args.args
    tracking = reading.payload.tracking
    assert tracking.stop_reason == stop_reason
    assert tracking.yaw_sequence.model_dump() == yaw_sequence


def fixed_yaw_telemetry():
    return {
        "clock_set": True,
        "referenced": True,
        "calibrating": False,
        "armed": True,
        "reachable": True,
        "status": "tracking",
        "demo_speed": 600,
        "solar_time": "2060-06-15T07:45:30+08:00",
    }


async def test_fixed_yaw_solar_time_does_not_become_sample_timestamp(ingestion):
    payload = telemetry()
    payload["tracking"]["fixed_yaw"] = fixed_yaw_telemetry()
    body = envelope(payload)
    body["occurred_at"] = datetime.now(UTC).isoformat()

    await ingestion._handle_message(mqtt_message(f"{TOPIC}/telemetry", body))

    reading, _ = ingestion._persist_solar_telemetry.await_args.args
    assert reading.occurred_at.isoformat() == body["occurred_at"]
    assert reading.payload.tracking.fixed_yaw.solar_time.isoformat() == (
        payload["tracking"]["fixed_yaw"]["solar_time"]
    )


@pytest.mark.parametrize(
    "invalid_values",
    [
        {"solar_time": "2060-06-15T07:45:30"},
        {"solar_time": 1_790_000_000},
        {"demo_speed": 0},
        {"demo_speed": 601},
    ],
)
async def test_rejects_invalid_fixed_yaw_telemetry(ingestion, invalid_values):
    payload = telemetry()
    payload["tracking"]["fixed_yaw"] = {**fixed_yaw_telemetry(), **invalid_values}

    await ingestion._handle_message(mqtt_message(f"{TOPIC}/telemetry", envelope(payload)))

    ingestion._persist_solar_telemetry.assert_not_awaited()


async def test_gps_disabled_telemetry_keeps_angle_feedback_and_power_while_waiting_for_clock(
    ingestion,
):
    payload = telemetry()
    payload["gps"]["enabled"] = False
    payload["tracking"].update(
        stop_reason="waiting_for_clock",
        actual_azimuth=72.3,
        actual_zenith=25.0,
    )
    payload["sensors"]["ina219_available"] = True
    payload["solar_power"].update(voltage=12.0, current=0.5, power=6.0, energy=1.25)

    await ingestion._handle_message(mqtt_message(f"{TOPIC}/telemetry", envelope(payload)))

    ingestion._persist_solar_telemetry.assert_awaited_once()
    reading, _ = ingestion._persist_solar_telemetry.await_args.args
    assert reading.payload.gps.enabled is False
    assert reading.payload.gps.latitude is None
    assert reading.payload.tracking.stop_reason == "waiting_for_clock"
    assert reading.payload.tracking.actual_azimuth == pytest.approx(72.3)
    assert reading.payload.solar_power.power == pytest.approx(6.0)


@pytest.mark.parametrize(
    "gps_values",
    [
        {"fix": True, "latitude": 3.14, "longitude": 101.69, "last_fix_age_ms": 50},
        {"time_valid": True},
        {"last_fix_age_ms": 50},
    ],
)
async def test_gps_disabled_telemetry_rejects_gps_data(ingestion, gps_values):
    payload = telemetry()
    payload["gps"].update(enabled=False, **gps_values)

    await ingestion._handle_message(mqtt_message(f"{TOPIC}/telemetry", envelope(payload)))

    ingestion._persist_solar_telemetry.assert_not_awaited()


async def test_solar_rejects_wrong_identity_retained_readings_and_false_zeroes(ingestion):
    body = envelope(telemetry())
    other_source = {**body, "producer_id": "other-board"}
    await ingestion._handle_message(mqtt_message(f"{TOPIC}/telemetry", other_source))
    await ingestion._handle_message(mqtt_message(f"{TOPIC}/telemetry", body, retained=True))
    body["payload"]["solar_power"]["voltage"] = 0
    await ingestion._handle_message(mqtt_message(f"{TOPIC}/telemetry", body))
    ingestion._persist_solar_telemetry.assert_not_awaited()


def test_freshness_keeps_broker_outage_and_pre_reconnect_data_stale():
    now = datetime.now(UTC)
    received_at = now - timedelta(seconds=2)
    timeout = timedelta(seconds=5)

    def evaluate(*, observed_at=None, connected=True, connected_at=None):
        return reading_freshness(
            received_at,
            observed_at,
            now=now,
            broker_connected=connected,
            connected_at=connected_at,
            stale_after=timeout,
        )

    assert evaluate() == Freshness.FRESH
    assert evaluate(connected=False) == Freshness.STALE
    assert evaluate(connected_at=now - timedelta(seconds=1)) == Freshness.STALE
    assert evaluate(observed_at=now - timedelta(seconds=6)) == Freshness.STALE


@pytest.mark.parametrize("include_history", [False, True])
@pytest.mark.parametrize(
    ("availability", "receipt_age", "sample_age", "expected"),
    [
        ("offline", 0, 0, Freshness.STALE),
        ("online", 1, 6, Freshness.FRESH),
        ("online", 6, 8, Freshness.STALE),
        ("online", 1, 36, Freshness.STALE),
    ],
)
async def test_monitor_reports_solar_freshness_independently_of_relay_state(
    include_history, availability, receipt_age, sample_age, expected
):
    now = datetime.now(UTC)
    payload = telemetry()
    reading = {
        "message_id": str(uuid4()),
        "occurred_at": (now - timedelta(seconds=sample_age)).isoformat(),
        "received_at": (now - timedelta(seconds=receipt_age)).isoformat(),
        "payload": payload,
    }
    session = AsyncMock()
    session.execute.return_value = MagicMock()
    session.execute.return_value.mappings.return_value.one_or_none.return_value = {
        "solar_status": {"status": availability, "boot_id": payload["boot_id"]},
        "solar_readings": [reading],
        "relay_received_at": now,
        "relay_occurred_at": now,
        "relay_online": True,
        "relay_offline": False,
        "classroom_telemetry": None,
        "energy_history": None,
        "classroom_meter_history": None,
    }

    snapshot = await read_prototype_snapshot(
        session,
        LOCAL_CLASSROOM_ID,
        settings(solar_stale_after_seconds=5),
        broker_connected=True,
        broker_connected_at=now - timedelta(seconds=60),
        include_history=include_history,
    )

    assert snapshot.solar.availability == availability
    assert snapshot.solar.freshness == expected
    assert snapshot.relay.availability == "online"
    assert snapshot.relay.freshness == Freshness.FRESH
    assert (snapshot.history is not None) is include_history
    assert (snapshot.classroom_meter_history is not None) is include_history


async def test_monitor_routes_enforce_the_private_bridge_and_serve_sse(monkeypatch):
    secret = "test-private-monitor-token-32-plus-characters"
    app = FastAPI()
    app.add_middleware(PrivateBridgeMiddleware)
    app.include_router(prototype_monitor.router)
    app.state.analytics_settings = settings(
        classroom_api_proxy_enabled=True, classroom_api_proxy_token=secret
    )
    app.state.sessions = MagicMock()
    app.state.mqtt = SimpleNamespace(connected=False, connected_at=None)
    snapshot = prototype_monitor.PrototypeSnapshot.model_validate(
        {
            "broker_connected": False,
            "solar": {
                "availability": "unknown",
                "freshness": "unknown",
                "last_received_at": None,
                "telemetry": None,
            },
            "relay": {"availability": "unknown", "freshness": "unknown", "last_received_at": None},
            "history": [],
            "classroom_meter_history": [],
        }
    )
    read = AsyncMock(return_value=snapshot)
    monkeypatch.setattr(prototype_monitor, "read_prototype_snapshot", read)
    monkeypatch.setattr(prototype_monitor, "STREAM_SECONDS", 0)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, client=("203.0.113.10", 1234)),
        base_url="http://test",
    ) as client:
        denied = await client.get(ROUTE)
        assert denied.status_code == 403
        read.assert_not_awaited()

        headers = {PROXY_TOKEN_HEADER: secret}
        data = await client.get(ROUTE, headers=headers)
        stream = await client.get(f"{ROUTE}/stream", headers=headers)
        unrelated = await client.get(ROUTE.replace(str(LOCAL_CLASSROOM_ID), str(uuid4())),
                                     headers=headers)

    assert data.json()["history"] == []
    assert data.json()["classroom_meter_history"] == []
    assert data.headers["cache-control"] == "private, no-store"
    assert stream.headers["content-type"].startswith("text/event-stream")
    assert stream.text.startswith("event: snapshot\ndata: ")
    assert "history" not in stream.text
    assert unrelated.status_code == 403
