"""Paho MQTT environment telemetry consumer."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from concurrent.futures import Future
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import paho.mqtt.client as mqtt
from pydantic import TypeAdapter, ValidationError

from app.schemas.mqtt import (
    AvailabilityPayload,
    CommandAcknowledgementPayload,
    DeviceStatePayload,
    EnvironmentTelemetryPayload,
    EnvironmentTelemetryPayloadV2,
    MqttEnvelope,
    MqttEnvelopeV2,
    OccupancyObservationPayload,
)

logger = logging.getLogger(__name__)
EnvelopeAdapter = TypeAdapter(MqttEnvelope[EnvironmentTelemetryPayload])
EnvelopeV2Adapter = TypeAdapter(MqttEnvelopeV2[EnvironmentTelemetryPayloadV2])
OccupancyObservationAdapter = TypeAdapter(MqttEnvelope[OccupancyObservationPayload])
DeviceStateAdapter = TypeAdapter(MqttEnvelope[DeviceStatePayload])
AvailabilityAdapter = TypeAdapter(MqttEnvelope[AvailabilityPayload])
AcknowledgementAdapter = TypeAdapter(MqttEnvelope[CommandAcknowledgementPayload])
EnvironmentEnvelope = (
    MqttEnvelope[EnvironmentTelemetryPayload] | MqttEnvelopeV2[EnvironmentTelemetryPayloadV2]
)
Persist = Callable[[EnvironmentEnvelope], Awaitable[bool]]
PersistOccupancyObservation = Callable[[MqttEnvelope[OccupancyObservationPayload]], Awaitable[bool]]
PersistDeviceState = Callable[[MqttEnvelope[DeviceStatePayload], UUID, bool], Awaitable[None]]
PersistGatewayAvailability = Callable[[MqttEnvelope[AvailabilityPayload]], Awaitable[None]]
PersistAcknowledgement = Callable[[MqttEnvelope[CommandAcknowledgementPayload]], Awaitable[None]]


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
        persist_occupancy_observation: PersistOccupancyObservation | None = None,
        persist_device_state: PersistDeviceState | None = None,
        persist_gateway_availability: PersistGatewayAvailability | None = None,
        persist_acknowledgement: PersistAcknowledgement | None = None,
    ) -> None:
        root = topic_root.rstrip("/")
        self.topics = [
            f"{root}/+/+/telemetry/environment",
            f"{root}/+/+/occupancy/observation",
            f"{root}/+/+/device/+/state",
            f"{root}/+/+/command/+/ack",
            f"{root}/+/+/gateway/+/status",
        ]
        self._topic_root = root.split("/")
        self._loop: asyncio.AbstractEventLoop | None = None
        self._persist = persist
        self._persist_occupancy_observation = persist_occupancy_observation
        self._persist_device_state = persist_device_state
        self._persist_gateway_availability = persist_gateway_availability
        self._persist_acknowledgement = persist_acknowledgement
        self.connected = False
        self._stopping = False
        self._client = mqtt.Client(
            callback_api_version=mqtt.CallbackAPIVersion.VERSION2,  # type: ignore[attr-defined]
            client_id=client_id,
        )
        if username is not None:
            self._client.username_pw_set(username, password)
        if tls_enabled:
            self._client.tls_set()
        self._client.reconnect_delay_set(min_delay=1, max_delay=30)
        self._client.on_connect = self._on_connect
        self._client.on_connect_fail = self._on_connect_fail
        self._client.on_subscribe = self._on_subscribe
        self._client.on_disconnect = self._on_disconnect
        self._client.on_message = self._on_message
        self._host = host
        self._port = port

    async def start(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._stopping = False
        # Let Paho retry the first connection too, so a broker outage cannot
        # prevent the API from starting and reporting degraded readiness.
        self._client.connect_async(self._host, self._port, keepalive=30)
        result = self._client.loop_start()
        if result != mqtt.MQTT_ERR_SUCCESS:
            raise RuntimeError(f"MQTT ingestion network loop failed to start: {result}")

    async def stop(self) -> None:
        self._stopping = True
        self.connected = False
        self._loop = None
        try:
            # Disconnect even while offline to cancel pending connection retries.
            await asyncio.to_thread(self._client.disconnect)
        finally:
            await asyncio.to_thread(self._client.loop_stop)

    def _on_connect_fail(self, _client: mqtt.Client, _userdata: Any) -> None:
        self.connected = False
        if not self._stopping:
            logger.warning("MQTT connection failed to %s:%s; retrying", self._host, self._port)

    def _on_connect(
        self,
        _client: mqtt.Client,
        _userdata: Any,
        _flags: Any,
        reason_code: Any,
        *_args: Any,
    ) -> None:
        self.connected = False
        if self._stopping:
            return
        if getattr(reason_code, "is_failure", False):
            logger.error("MQTT connection rejected: %s", reason_code)
            return
        self.connected = True
        logger.info("MQTT connected; subscribing to %d topics", len(self.topics))
        for topic in self.topics:
            result, message_id = _client.subscribe(topic, qos=1)
            if result != mqtt.MQTT_ERR_SUCCESS:
                logger.error("MQTT subscription request failed for %s: rc=%s", topic, result)
            else:
                logger.debug("MQTT subscription requested for %s (mid=%s)", topic, message_id)

    def _on_subscribe(
        self,
        _client: mqtt.Client,
        _userdata: Any,
        message_id: int,
        reason_codes: list[Any],
        *_args: Any,
    ) -> None:
        for reason_code in reason_codes:
            if getattr(reason_code, "is_failure", False):
                logger.error(
                    "MQTT subscription rejected (mid=%s): %s",
                    message_id,
                    reason_code,
                )
            else:
                logger.info(
                    "MQTT subscription granted (mid=%s, qos=%s)",
                    message_id,
                    getattr(reason_code, "value", reason_code),
                )

    def _on_disconnect(
        self,
        _client: mqtt.Client,
        _userdata: Any,
        _disconnect_flags: Any,
        reason_code: Any,
        *_args: Any,
    ) -> None:
        self.connected = False
        if not self._stopping:
            logger.warning("MQTT disconnected: %s; reconnecting", reason_code)

    def _on_message(self, _client: mqtt.Client, _userdata: Any, message: mqtt.MQTTMessage) -> None:
        if self._loop is None or self._loop.is_closed():
            return
        future = asyncio.run_coroutine_threadsafe(self._handle_message(message), self._loop)
        future.add_done_callback(self._log_task_error)

    async def _handle_message(self, message: mqtt.MQTTMessage) -> None:
        try:
            payload = json.loads(message.payload.decode("utf-8"))
            parts = message.topic.split("/")
            if len(parts) < 4 or parts[: len(self._topic_root)] != self._topic_root:
                raise ValueError("unexpected MQTT topic root")
            if len(parts) == 6 and parts[-2:] == ["telemetry", "environment"]:
                schema_version = (
                    payload.get("schema_version") if isinstance(payload, dict) else None
                )
                if isinstance(schema_version, str) and schema_version.startswith("2."):
                    environment_envelope: EnvironmentEnvelope = EnvelopeV2Adapter.validate_python(
                        payload
                    )
                else:
                    environment_envelope = EnvelopeAdapter.validate_python(payload)
                self._validate_topic_identity(environment_envelope, parts)
                inserted = await self._persist(environment_envelope)
                logger.debug("Environment telemetry %s", "stored" if inserted else "duplicate")
            elif len(parts) == 6 and parts[-2:] == ["occupancy", "observation"]:
                occupancy_envelope = OccupancyObservationAdapter.validate_python(payload)
                self._validate_topic_identity(occupancy_envelope, parts)
                if (
                    self._persist_occupancy_observation is None
                    or occupancy_envelope.payload.count < 0
                ):
                    raise ValueError("occupancy observation is invalid or handler is missing")
                logger.debug(
                    "Received occupancy observation classroom_id=%s count=%d",
                    occupancy_envelope.classroom_id,
                    occupancy_envelope.payload.count,
                )
                inserted = await self._persist_occupancy_observation(occupancy_envelope)
                logger.info(
                    "Occupancy observation %s classroom_id=%s count=%d",
                    "stored" if inserted else "duplicate",
                    occupancy_envelope.classroom_id,
                    occupancy_envelope.payload.count,
                )
            elif len(parts) == 7 and parts[4] == "device" and parts[6] == "state":
                device_state_envelope = DeviceStateAdapter.validate_python(payload)
                self._validate_topic_identity(device_state_envelope, parts)
                if (
                    str(device_state_envelope.payload.device_id) != parts[5]
                    or self._persist_device_state is None
                ):
                    raise ValueError("device-state identity or handler is invalid")
                await self._persist_device_state(
                    device_state_envelope,
                    UUID(parts[5]),
                    message.retain,
                )
            elif len(parts) == 7 and parts[4] == "command" and parts[6] == "ack":
                acknowledgement_envelope = AcknowledgementAdapter.validate_python(payload)
                self._validate_topic_identity(acknowledgement_envelope, parts)
                if (
                    str(acknowledgement_envelope.payload.device_id) != parts[5]
                    or self._persist_acknowledgement is None
                    or message.retain
                ):
                    raise ValueError("command acknowledgement identity or delivery mode is invalid")
                await self._persist_acknowledgement(acknowledgement_envelope)
            elif len(parts) == 7 and parts[4] == "gateway" and parts[6] == "status":
                availability_envelope = AvailabilityAdapter.validate_python(payload)
                self._validate_topic_identity(availability_envelope, parts)
                if (
                    availability_envelope.producer_id != parts[5]
                    or self._persist_gateway_availability is None
                ):
                    raise ValueError("gateway availability identity or handler is invalid")
                await self._persist_gateway_availability(availability_envelope)
            else:
                raise ValueError("unexpected MQTT topic")
        except (ValidationError, ValueError, json.JSONDecodeError) as exc:
            logger.warning("Rejected MQTT message topic=%s: %s", message.topic, exc)
        except Exception:
            logger.exception("Failed to process MQTT message topic=%s", message.topic)

    def _validate_topic_identity(self, envelope: Any, parts: list[str]) -> None:
        offset = len(self._topic_root)
        if (
            len(parts) < offset + 2
            or str(envelope.site_id) != parts[offset]
            or str(envelope.classroom_id) != parts[offset + 1]
        ):
            raise ValueError("topic identity does not match envelope")
        if envelope.occurred_at > datetime.now(UTC) + timedelta(seconds=30):
            raise ValueError("MQTT timestamp is too far in the future")

    @staticmethod
    def _log_task_error(future: Future[None]) -> None:
        if not future.cancelled() and future.exception() is not None:
            logger.error("MQTT ingestion task failed: %s", future.exception())
