from __future__ import annotations

import json
from unittest import IsolatedAsyncioTestCase
from uuid import UUID, uuid4

import paho.mqtt.client as mqtt
from sqlalchemy.dialects.postgresql import ENUM, dialect
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.core.db import occupancy_current
from app.integrations.mqtt.ingestion import MqttIngestion


class MqttIngestionTests(IsolatedAsyncioTestCase):
    def test_occupancy_current_status_uses_database_enum_type(self) -> None:
        status_type = occupancy_current.c.status.type
        self.assertIsInstance(status_type, ENUM)
        self.assertEqual(status_type.name, "occupancy_status")
        self.assertFalse(status_type.create_type)

        statement = pg_insert(occupancy_current).values(
            classroom_id=uuid4(),
            status="possibly_vacant",
            person_count=0,
        )
        compiled_statement = statement.compile(dialect=dialect())
        self.assertIsInstance(compiled_statement.binds["status"].type, ENUM)
        self.assertNotIn("::VARCHAR", str(compiled_statement))

    async def test_valid_environment_message_is_persisted(self) -> None:
        persisted = []

        async def persist(envelope):
            persisted.append(envelope)
            return True

        ingestion = MqttIngestion(
            host="localhost",
            port=1883,
            topic_root="aceo/v1",
            client_id="test",
            persist=persist,
        )
        site_id, classroom_id = uuid4(), uuid4()
        payload = {
            "schema_version": "1.0",
            "message_id": str(uuid4()),
            "occurred_at": "2026-08-14T03:30:00Z",
            "site_id": str(site_id),
            "classroom_id": str(classroom_id),
            "producer_id": "sensor-gateway",
            "payload": {
                "sensor_id": "env-01",
                "temperature_c": 26.4,
                "relative_humidity_pct": 66.0,
                "illuminance_lux": 240.0,
                "sample_interval_ms": 5000,
            },
        }
        message = mqtt.MQTTMessage()
        message.topic = f"aceo/v1/{site_id}/{classroom_id}/telemetry/environment".encode()
        message.payload = json.dumps(payload).encode()

        await ingestion._handle_message(message)

        self.assertEqual(len(persisted), 1)
        self.assertEqual(persisted[0].site_id, UUID(str(site_id)))

    async def test_schema_v2_raw_light_and_pir_message_is_persisted(self) -> None:
        persisted = []

        async def persist(envelope):
            persisted.append(envelope)
            return True

        ingestion = MqttIngestion(
            host="localhost",
            port=1883,
            topic_root="aceo/v1",
            client_id="test",
            persist=persist,
        )
        site_id, classroom_id = uuid4(), uuid4()
        payload = {
            "schema_version": "2.0",
            "message_id": str(uuid4()),
            "occurred_at": "2026-10-03T13:00:00Z",
            "site_id": str(site_id),
            "classroom_id": str(classroom_id),
            "producer_id": "esp32-room-a204",
            "payload": {
                "sensor_id": "esp32-room-a204",
                "temperature_c": 26.4,
                "relative_humidity_pct": 66.0,
                "illuminance_adc": 2048,
                "motion_detected": True,
                "sample_interval_ms": 5000,
            },
        }
        message = mqtt.MQTTMessage()
        message.topic = f"aceo/v1/{site_id}/{classroom_id}/telemetry/environment".encode()
        message.payload = json.dumps(payload).encode()

        await ingestion._handle_message(message)

        self.assertEqual(len(persisted), 1)
        self.assertEqual(persisted[0].schema_version, "2.0")
        self.assertEqual(persisted[0].payload.illuminance_adc, 2048)
        self.assertTrue(persisted[0].payload.motion_detected)

    async def test_valid_gateway_availability_message_is_persisted(self) -> None:
        persisted = []

        async def persist(_envelope):
            return True

        async def persist_availability(envelope):
            persisted.append(envelope)

        ingestion = MqttIngestion(
            host="localhost",
            port=1883,
            topic_root="aceo/v1",
            client_id="test",
            persist=persist,
            persist_gateway_availability=persist_availability,
        )
        site_id, classroom_id = uuid4(), uuid4()
        payload = {
            "schema_version": "1.0",
            "message_id": str(uuid4()),
            "occurred_at": "2026-10-24T03:30:00Z",
            "site_id": str(site_id),
            "classroom_id": str(classroom_id),
            "producer_id": "esp32-room-a204",
            "payload": {
                "status": "offline",
                "boot_id": str(uuid4()),
                "firmware_version": "1.1.0",
            },
        }
        message = mqtt.MQTTMessage()
        message.topic = (
            f"aceo/v1/{site_id}/{classroom_id}/gateway/esp32-room-a204/status".encode()
        )
        message.payload = json.dumps(payload).encode("utf-8")
        message.retain = True

        await ingestion._handle_message(message)

        self.assertEqual(len(persisted), 1)
        self.assertEqual(persisted[0].payload.status.value, "offline")
        self.assertEqual(persisted[0].producer_id, "esp32-room-a204")

    async def test_topic_identity_mismatch_is_rejected(self) -> None:
        persisted = []

        async def persist(envelope):
            persisted.append(envelope)
            return True

        ingestion = MqttIngestion(
            host="localhost",
            port=1883,
            topic_root="aceo/v1",
            client_id="test",
            persist=persist,
        )
        site_id, classroom_id = uuid4(), uuid4()
        payload = {
            "schema_version": "1.0",
            "message_id": str(uuid4()),
            "occurred_at": "2026-08-14T03:30:00Z",
            "site_id": str(site_id),
            "classroom_id": str(classroom_id),
            "producer_id": "sensor-gateway",
            "payload": {
                "sensor_id": "env-01",
                "temperature_c": 26.4,
                "relative_humidity_pct": 66.0,
                "illuminance_lux": 240.0,
                "sample_interval_ms": 5000,
            },
        }
        message = mqtt.MQTTMessage()
        message.topic = f"aceo/v1/{site_id}/{uuid4()}/telemetry/environment".encode()
        message.payload = json.dumps(payload).encode()

        await ingestion._handle_message(message)

        self.assertEqual(persisted, [])

    async def test_valid_occupancy_message_is_persisted(self) -> None:
        persisted = []

        async def persist(_envelope):
            return True

        async def persist_occupancy(envelope):
            persisted.append(envelope)
            return True

        ingestion = MqttIngestion(
            host="localhost",
            port=1883,
            topic_root="aceo/v1",
            client_id="test",
            persist=persist,
            persist_occupancy_observation=persist_occupancy,
        )
        site_id, classroom_id = uuid4(), uuid4()
        payload = {
            "schema_version": "1.0",
            "message_id": str(uuid4()),
            "occurred_at": "2026-08-14T03:30:00Z",
            "site_id": str(site_id),
            "classroom_id": str(classroom_id),
            "producer_id": "cv-worker-01",
            "payload": {
                "camera_id": "camera-01",
                "count": 3,
                "confidence": 0.96,
                "entries_delta": 1,
                "exits_delta": 0,
                "frame_sequence": 84591,
                "source": "roboflow_bytetrack",
            },
        }
        message = mqtt.MQTTMessage()
        message.topic = f"aceo/v1/{site_id}/{classroom_id}/occupancy/observation".encode()
        message.payload = json.dumps(payload).encode()

        await ingestion._handle_message(message)

        self.assertEqual(len(persisted), 1)
        self.assertEqual(persisted[0].payload.count, 3)

    async def test_device_state_is_validated_and_sent_to_state_persistence(self) -> None:
        persisted = []
        site_id, classroom_id, device_id = uuid4(), uuid4(), uuid4()

        async def persist(_envelope):
            return True

        async def persist_state(envelope, topic_device_id, retained):
            persisted.append((envelope, topic_device_id, retained))

        ingestion = MqttIngestion(
            host="localhost",
            port=1883,
            topic_root="aceo/v1",
            client_id="test",
            persist=persist,
            persist_device_state=persist_state,
        )
        payload = {
            "schema_version": "1.0",
            "message_id": str(uuid4()),
            "occurred_at": "2026-10-22T10:00:00Z",
            "site_id": str(site_id),
            "classroom_id": str(classroom_id),
            "producer_id": "esp32-room-a204",
            "payload": {
                "device_id": str(device_id),
                "state": {"on": True, "mode": "manual"},
                "fault_codes": [],
                "state_version": 1,
            },
        }
        message = mqtt.MQTTMessage()
        message.topic = f"aceo/v1/{site_id}/{classroom_id}/device/{device_id}/state".encode()
        message.payload = json.dumps(payload).encode()

        await ingestion._handle_message(message)

        self.assertEqual(len(persisted), 1)
        self.assertEqual(persisted[0][1], device_id)
        self.assertTrue(persisted[0][0].payload.state["on"])
        self.assertFalse(persisted[0][2])

    async def test_ack_topic_device_mismatch_is_rejected(self) -> None:
        persisted = []

        async def persist(_envelope):
            return True

        async def persist_ack(envelope):
            persisted.append(envelope)

        ingestion = MqttIngestion(
            host="localhost",
            port=1883,
            topic_root="aceo/v1",
            client_id="test",
            persist=persist,
            persist_acknowledgement=persist_ack,
        )
        site_id, classroom_id, topic_device_id = uuid4(), uuid4(), uuid4()
        payload = {
            "schema_version": "1.0",
            "message_id": str(uuid4()),
            "occurred_at": "2026-10-22T10:00:00Z",
            "site_id": str(site_id),
            "classroom_id": str(classroom_id),
            "producer_id": "esp32-room-a204",
            "payload": {
                "command_id": str(uuid4()),
                "device_id": str(uuid4()),
                "status": "rejected",
                "reason_code": "pir_master_no_motion",
                "reported_state": {"on": False, "mode": "auto"},
            },
        }
        message = mqtt.MQTTMessage()
        message.topic = f"aceo/v1/{site_id}/{classroom_id}/command/{topic_device_id}/ack".encode()
        message.payload = json.dumps(payload).encode()

        await ingestion._handle_message(message)

        self.assertEqual(persisted, [])
