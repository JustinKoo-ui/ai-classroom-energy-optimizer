from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import paho.mqtt.client as mqtt
import pytest

from app.core.config import Settings
from app.domain.local_hardware import LOCAL_CLASSROOM_ID, LOCAL_SITE_ID
from app.integrations.mqtt.ingestion import MqttIngestion
from app.repositories.environment import insert_environment_reading
from app.repositories.solar_monitor import read_prototype_snapshot
from app.schemas.classroom_telemetry import ClassroomTelemetryEnvelope
from app.schemas.common import Freshness

PRODUCER = "esp32-room-a204"
TOPIC_ROOT = f"aceo/v1/{LOCAL_SITE_ID}/{LOCAL_CLASSROOM_ID}"


@pytest.fixture
def body():
    return {
        "schema_version": "3.0",
        "message_id": str(uuid4()),
        "occurred_at": None,
        "site_id": str(LOCAL_SITE_ID),
        "classroom_id": str(LOCAL_CLASSROOM_ID),
        "producer_id": PRODUCER,
        "payload": {
            "sensor_id": PRODUCER,
            "boot_id": str(uuid4()),
            "uptime_ms": 100000,
            "sample_interval_ms": 2000,
            "temperature_c": 27,
            "relative_humidity_pct": 55.5,
            "illuminance_lux": 199,
            "motion_detected": True,
            "voltage_v": None,
            "current_a": None,
            "active_power_w": None,
            "energy_wh_total": 1.25,
            "energy_kwh_total": 0.00125,
            "relays": {"light": False, "fan": True, "air_conditioner": True},
            "sensors": {
                "dht22_available": True,
                "pir_ready": True,
                "bh1750_available": False,
                "bh1750_fresh": False,
                "bh1750_age_ms": 6000,
                "ina219_available": False,
            },
            "energy_storage_available": True,
        },
    }


@pytest.fixture
def ingestion():
    return MqttIngestion(
        host="localhost",
        port=1883,
        topic_root="aceo/v1",
        client_id="test-classroom-telemetry",
        persist=AsyncMock(return_value=True),
        persist_gateway_availability=AsyncMock(),
    )


def message(body, *, suffix="telemetry/environment", retained=False):
    incoming = mqtt.MQTTMessage()
    incoming.topic = f"{TOPIC_ROOT}/{suffix}".encode()
    incoming.payload = json.dumps(body).encode()
    incoming.retain = retained
    return incoming


async def test_ingestion_accepts_partial_telemetry_and_stale_lux(ingestion, body):
    await ingestion._handle_message(message(body))

    ingestion._persist.assert_awaited_once()
    envelope = ingestion._persist.await_args.args[0]
    assert envelope.occurred_at is None
    assert envelope.payload.relays.air_conditioner is True
    assert envelope.payload.voltage_v is None
    assert envelope.payload.energy_wh_total == 1.25
    assert envelope.payload.illuminance_lux == 199
    assert envelope.payload.sensors.bh1750_fresh is False


@pytest.mark.parametrize(
    "changes",
    [
        {"voltage_v": 0},
        {"humidity_pct": 30},
        {"temperature_c": "27"},
        {"energy_kwh_total": 1.25},
        {"illuminance_lux": None},
        {"energy_wh_total": float("inf")},
    ],
)
async def test_ingestion_rejects_inconsistent_or_invalid_values(ingestion, body, changes):
    body["payload"].update(changes)

    await ingestion._handle_message(message(body))

    ingestion._persist.assert_not_awaited()


async def test_ingestion_rejects_voltage_above_ina219_limit(ingestion, body):
    payload = body["payload"]
    payload["sensors"]["ina219_available"] = True
    payload.update(voltage_v=26.004, current_a=0.1, active_power_w=2.6004)

    await ingestion._handle_message(message(body))

    ingestion._persist.assert_not_awaited()


async def test_ingestion_rejects_wrong_identity_retained_messages_and_numeric_clock(
    ingestion, body
):
    await ingestion._handle_message(message(body, retained=True))
    await ingestion._handle_message(message({**body, "classroom_id": str(uuid4())}))
    await ingestion._handle_message(message({**body, "occurred_at": 123}))
    ingestion._persist.assert_not_awaited()


async def test_gateway_null_clock_uses_receipt_time_only_on_status_topic(ingestion, body):
    body["schema_version"] = "1.0"
    body["payload"] = {
        "status": "offline",
        "boot_id": str(uuid4()),
        "firmware_version": "1.2.0",
    }
    before = datetime.now(UTC)
    await ingestion._handle_message(
        message(body, suffix=f"gateway/{PRODUCER}/status", retained=True)
    )

    ingestion._persist_gateway_availability.assert_awaited_once()
    envelope = ingestion._persist_gateway_availability.await_args.args[0]
    assert before <= envelope.occurred_at <= datetime.now(UTC)
    assert envelope.payload.status.value == "offline"


async def test_persistence_projects_partial_sensor_data_without_copying_stale_lux(body):
    envelope = ClassroomTelemetryEnvelope.model_validate(body)
    session = AsyncMock()
    session.execute.return_value = MagicMock()
    session.execute.return_value.scalar_one_or_none.side_effect = [envelope.message_id, None]

    assert await insert_environment_reading(session, envelope) is True
    assert await insert_environment_reading(session, envelope) is False
    statement, parameters = session.execute.await_args.args
    assert "ON CONFLICT (message_id) DO NOTHING" in statement.text
    assert parameters["temperature_c"] == 27
    assert parameters["fresh_lux"] is None
    assert parameters["device_occurred_at"] is None
    assert parameters["occurred_at"] == parameters["received_at"]
    assert json.loads(parameters["payload"])["illuminance_lux"] == 199


@pytest.mark.parametrize(
    ("broker_connected", "relay_offline", "receipt_age", "expected"),
    [
        (True, False, 1, Freshness.FRESH),
        (True, True, 1, Freshness.STALE),
        (True, False, 16, Freshness.STALE),
        (False, False, 1, Freshness.STALE),
    ],
)
async def test_dashboard_snapshot_preserves_latest_real_values_and_counter_on_fault(
    body, broker_connected, relay_offline, receipt_age, expected
):
    now = datetime.now(UTC)
    received_at = now - timedelta(seconds=receipt_age)
    session = AsyncMock()
    session.execute.return_value = MagicMock()
    session.execute.return_value.mappings.return_value.one_or_none.return_value = {
        "solar_status": None,
        "solar_readings": None,
        "relay_received_at": now,
        "relay_occurred_at": now,
        "relay_online": not relay_offline,
        "relay_offline": relay_offline,
        "classroom_telemetry": {
            "message_id": body["message_id"],
            "occurred_at": None,
            "received_at": received_at,
            "payload": body["payload"],
        },
        "energy_history": [
            {"received_at": received_at - timedelta(seconds=2), "energy_wh_total": 1.25}
        ],
        "classroom_meter_history": [
            {
                "received_at": received_at - timedelta(seconds=2),
                "boot_id": body["payload"]["boot_id"],
                "active_power_w": 0,
                "energy_wh_total": 1.25,
            },
            {
                "received_at": received_at,
                "boot_id": body["payload"]["boot_id"],
                "active_power_w": None,
                "energy_wh_total": None,
            },
        ],
    }

    snapshot = await read_prototype_snapshot(
        session,
        LOCAL_CLASSROOM_ID,
        Settings(_env_file=None, database_url="postgresql+asyncpg://unused"),
        broker_connected=broker_connected,
        broker_connected_at=now - timedelta(seconds=60),
        include_history=True,
    )

    assert snapshot is not None
    assert snapshot.classroom_telemetry_freshness == expected
    assert snapshot.classroom_telemetry is not None
    assert snapshot.classroom_telemetry.payload.active_power_w is None
    assert snapshot.classroom_telemetry.payload.energy_kwh_total == 0.00125
    assert snapshot.energy_history is not None
    assert snapshot.energy_history[0].energy_wh_total == 1.25
    assert snapshot.classroom_meter_history is not None
    assert snapshot.classroom_meter_history[0].active_power_w == 0
    assert snapshot.classroom_meter_history[1].active_power_w is None
    assert snapshot.classroom_meter_history[1].energy_wh_total is None
