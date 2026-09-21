"""Paho MQTT environment telemetry consumer."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any

import paho.mqtt.client as mqtt
from pydantic import TypeAdapter, ValidationError

from app.repositories.environment import insert_environment_reading
from app.schemas.mqtt import EnvironmentTelemetryPayload, MqttEnvelope

logger = logging.getLogger(__name__)
EnvelopeAdapter = TypeAdapter(MqttEnvelope[EnvironmentTelemetryPayload])
Persist = Callable[[MqttEnvelope[EnvironmentTelemetryPayload]], Awaitable[bool]]


class MqttIngestion:
    """Owns the MQTT network loop and bridges callbacks to the async database."""

    def __init__(
        self,
        *,
        host: str,
        port: int,
        topic_root: str,
        client_id: str,
        persist: Persist,
        tls_enabled: bool = False,
        username: str | None = None,
        password: str | None = None,
    ) -> None:
        self.topic = f"{topic_root.rstrip('/')}/+/+/telemetry/environment"
        self._loop: asyncio.AbstractEventLoop | None = None
        self._persist = persist
        self.connected = False
        self._client = mqtt.Client(
            callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
            client_id=client_id,
        )
        if username is not None:
            self._client.username_pw_set(username, password)
        if tls_enabled:
            self._client.tls_set()
        self._client.on_connect = self._on_connect
        self._client.on_disconnect = self._on_disconnect
        self._client.on_message = self._on_message
        self._host = host
        self._port = port

    async def start(self) -> None:
        self._loop = asyncio.get_running_loop()
        await asyncio.to_thread(self._client.connect, self._host, self._port)
        self._client.loop_start()

    async def stop(self) -> None:
        self.connected = False
        self._client.loop_stop()
        if self._client.is_connected():
            await asyncio.to_thread(self._client.disconnect)

    def _on_connect(self, _client: mqtt.Client, _userdata: Any, _flags: Any, reason_code: Any, *_args: Any) -> None:
        if getattr(reason_code, "is_failure", False):
            logger.error("MQTT connection rejected: %s", reason_code)
            return
        self.connected = True
        _client.subscribe(self.topic, qos=1)

    def _on_disconnect(self, _client: mqtt.Client, _userdata: Any, _disconnect_flags: Any, reason_code: Any, *_args: Any) -> None:
        self.connected = False
        if reason_code:
            logger.warning("MQTT disconnected: %s", reason_code)

    def _on_message(self, _client: mqtt.Client, _userdata: Any, message: mqtt.MQTTMessage) -> None:
        if self._loop is None or self._loop.is_closed():
            return
        future = asyncio.run_coroutine_threadsafe(self._handle_message(message), self._loop)
        future.add_done_callback(self._log_task_error)

    async def _handle_message(self, message: mqtt.MQTTMessage) -> None:
        try:
            envelope = EnvelopeAdapter.validate_json(message.payload)
            parts = message.topic.split("/")
            if len(parts) != 6 or parts[-2:] != ["telemetry", "environment"]:
                raise ValueError("unexpected environment telemetry topic")
            if str(envelope.site_id) != parts[2] or str(envelope.classroom_id) != parts[3]:
                raise ValueError("topic identity does not match envelope")
            inserted = await self._persist(envelope)
            logger.debug("Environment telemetry %s", "stored" if inserted else "duplicate")
        except (ValidationError, ValueError, json.JSONDecodeError) as exc:
            logger.warning("Rejected MQTT environment telemetry: %s", exc)
        except Exception:
            logger.exception("Failed to persist MQTT environment telemetry")

    @staticmethod
    def _log_task_error(future: asyncio.Future[None]) -> None:
        if not future.cancelled() and future.exception() is not None:
            logger.exception("MQTT ingestion task failed", exc_info=future.exception())
