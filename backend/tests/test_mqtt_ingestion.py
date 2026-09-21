from __future__ import annotations

import json
from unittest import IsolatedAsyncioTestCase
from uuid import UUID, uuid4

import paho.mqtt.client as mqtt

from app.integrations.mqtt.ingestion import MqttIngestion


class MqttIngestionTests(IsolatedAsyncioTestCase):
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
