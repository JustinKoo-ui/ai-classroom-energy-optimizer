from __future__ import annotations

import asyncio
import json
import threading
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, Mock, call
from uuid import uuid4

import paho.mqtt.client as mqtt

from app.integrations.mqtt.dispatcher import MqttCommandDispatcher
from app.integrations.mqtt.ingestion import MqttIngestion


class MqttLifecycleTests(IsolatedAsyncioTestCase):
    def ingestion(self, persist: AsyncMock | None = None) -> MqttIngestion:
        return MqttIngestion(
            host="unavailable-broker.invalid",
            port=1883,
            topic_root="aceo/v1",
            client_id=f"test-ingestion-{uuid4()}",
            persist=persist or AsyncMock(return_value=True),
        )

    def dispatcher(self) -> MqttCommandDispatcher:
        return MqttCommandDispatcher(
            host="unavailable-broker.invalid",
            port=1883,
            topic_root="aceo/v1",
            client_id=f"test-dispatcher-{uuid4()}",
            producer_id="test-dispatcher",
            sessions=Mock(),
        )

    def client(self) -> Mock:
        client = Mock(spec=mqtt.Client)
        # A synchronous first connection would fail before the retry loop starts.
        client.connect.side_effect = OSError("broker is offline")
        client.loop_start.return_value = mqtt.MQTT_ERR_SUCCESS
        client.subscribe.return_value = (mqtt.MQTT_ERR_SUCCESS, 1)
        client.is_connected.return_value = False
        return client

    async def test_offline_start_keeps_api_available_without_dispatching_commands(self) -> None:
        for adapter in (self.ingestion(), self.dispatcher()):
            with self.subTest(adapter=type(adapter).__name__):
                client = self.client()
                adapter._client = client
                if isinstance(adapter, MqttCommandDispatcher):
                    adapter._dispatch_batch = AsyncMock()
                try:
                    await adapter.start()
                    adapter._on_connect_fail(client, None)
                    await asyncio.sleep(0)

                    self.assertFalse(adapter.connected)
                    client.connect.assert_not_called()
                    client.connect_async.assert_called_once_with(
                        "unavailable-broker.invalid", 1883, keepalive=30
                    )
                    client.loop_start.assert_called_once()
                    if isinstance(adapter, MqttCommandDispatcher):
                        adapter._dispatch_batch.assert_not_awaited()
                finally:
                    await adapter.stop()

    async def test_ingestion_resubscribes_and_persists_after_reconnection(self) -> None:
        received = asyncio.Event()

        async def persist(_envelope):
            received.set()
            return True

        stored = AsyncMock(side_effect=persist)
        ingestion = self.ingestion(stored)
        client = self.client()
        ingestion._client = client
        await ingestion.start()
        try:
            ingestion._on_connect(client, None, None, 0)
            self.assertTrue(ingestion.connected)
            ingestion._on_disconnect(client, None, None, "connection lost")
            self.assertFalse(ingestion.connected)
            ingestion._on_connect(client, None, None, 0)
            self.assertTrue(ingestion.connected)
            subscriptions = [call(topic, qos=1) for topic in ingestion.topics]
            self.assertEqual(client.subscribe.call_args_list, subscriptions * 2)

            site_id, classroom_id = uuid4(), uuid4()
            message = mqtt.MQTTMessage()
            message.topic = f"aceo/v1/{site_id}/{classroom_id}/telemetry/environment".encode()
            message.payload = json.dumps(
                {
                    "schema_version": "2.0",
                    "message_id": str(uuid4()),
                    "occurred_at": "2026-10-24T03:30:00Z",
                    "site_id": str(site_id),
                    "classroom_id": str(classroom_id),
                    "producer_id": "test-sensor",
                    "payload": {
                        "sensor_id": "test-sensor",
                        "temperature_c": 26.4,
                        "relative_humidity_pct": 66.0,
                        "illuminance_adc": 2048,
                        "motion_detected": True,
                        "sample_interval_ms": 5000,
                    },
                }
            ).encode()
            ingestion._on_message(client, None, message)
            await asyncio.wait_for(received.wait(), timeout=1)
            stored.assert_awaited_once()
        finally:
            await ingestion.stop()

    async def test_dispatcher_resumes_waiting_work_when_broker_returns(self) -> None:
        dispatcher = self.dispatcher()
        client = self.client()
        dispatcher._client = client
        dispatched = asyncio.Event()

        async def dispatch():
            dispatched.set()

        dispatcher._dispatch_batch = AsyncMock(side_effect=dispatch)
        await dispatcher.start()
        try:
            await asyncio.sleep(0)
            dispatcher._dispatch_batch.assert_not_awaited()
            dispatcher._on_connect(client, None, None, 0)
            await asyncio.wait_for(dispatched.wait(), timeout=1)
            dispatcher._dispatch_batch.assert_awaited_once()
        finally:
            await dispatcher.stop()

    async def test_shutdown_cancels_offline_retries_and_joins_outside_event_loop(self) -> None:
        event_loop_thread = threading.get_ident()
        for adapter in (self.ingestion(), self.dispatcher()):
            with self.subTest(adapter=type(adapter).__name__):
                client = self.client()
                adapter._client = client
                shutdown_steps: list[tuple[str, int]] = []

                def disconnect(steps=shutdown_steps):
                    steps.append(("disconnect", threading.get_ident()))

                def stop_loop(steps=shutdown_steps):
                    steps.append(("loop_stop", threading.get_ident()))

                client.disconnect.side_effect = disconnect
                client.loop_stop.side_effect = stop_loop
                await adapter.start()
                await adapter.stop()

                self.assertEqual([step for step, _ in shutdown_steps], ["disconnect", "loop_stop"])
                self.assertTrue(all(thread != event_loop_thread for _, thread in shutdown_steps))
                self.assertFalse(adapter.connected)
                # A late CONNACK during shutdown must not advertise recovery.
                adapter._on_connect(client, None, None, 0)
                self.assertFalse(adapter.connected)

    async def test_dispatcher_cleans_up_network_when_background_task_fails(self) -> None:
        dispatcher = self.dispatcher()
        client = self.client()
        dispatcher._client = client
        await dispatcher.start()
        assert dispatcher._task is not None
        dispatcher._task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await dispatcher._task

        async def failed_dispatch():
            raise RuntimeError("dispatch database unavailable")

        dispatcher._task = asyncio.create_task(failed_dispatch())
        with self.assertRaisesRegex(RuntimeError, "dispatch database unavailable"):
            await dispatcher.stop()
        client.disconnect.assert_called_once()
        client.loop_stop.assert_called_once()
