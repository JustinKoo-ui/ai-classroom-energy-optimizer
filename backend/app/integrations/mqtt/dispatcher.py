"""MQTT publisher for commands already persisted by the control policy."""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import paho.mqtt.client as mqtt
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

logger = logging.getLogger(__name__)


class MqttCommandDispatcher:
    """Publishes durable outbox rows; it never creates or edits policy decisions."""

    def __init__(
        self,
        *,
        host: str,
        port: int,
        topic_root: str,
        client_id: str,
        producer_id: str,
        sessions: async_sessionmaker[AsyncSession],
        username: str | None = None,
        password: str | None = None,
        tls_enabled: bool = False,
        ack_timeout_seconds: int = 10,
        max_retries: int = 3,
    ) -> None:
        self._topic_root = topic_root.rstrip("/")
        self._producer_id = producer_id
        self._sessions = sessions
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
        self._client.on_disconnect = self._on_disconnect
        self._host = host
        self._port = port
        self._ack_timeout = timedelta(seconds=ack_timeout_seconds)
        self._max_retries = max_retries
        self.connected = False
        self._wake_event = asyncio.Event()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._task: asyncio.Task[None] | None = None
        self._stopping = False

    async def start(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._stopping = False
        self._client.connect_async(self._host, self._port, keepalive=30)
        result = self._client.loop_start()
        if result != mqtt.MQTT_ERR_SUCCESS:
            raise RuntimeError(f"MQTT dispatcher network loop failed to start: {result}")
        self._task = asyncio.create_task(self._dispatch_loop())

    async def stop(self) -> None:
        self._stopping = True
        self.connected = False
        self._wake_event.set()
        try:
            if self._task is not None:
                await self._task
        finally:
            self._task = None
            self._loop = None
            try:
                await asyncio.to_thread(self._client.disconnect)
            finally:
                await asyncio.to_thread(self._client.loop_stop)

    def _on_connect_fail(self, _client: mqtt.Client, _userdata: Any) -> None:
        self.connected = False
        if not self._stopping:
            logger.warning(
                "MQTT dispatcher connection failed to %s:%s; retrying", self._host, self._port
            )

    def wake(self) -> None:
        if self._loop is not None and not self._loop.is_closed():
            self._loop.call_soon_threadsafe(self._wake_event.set)

    async def _dispatch_loop(self) -> None:
        while not self._stopping:
            if self.connected:
                await self._dispatch_batch()
            self._wake_event.clear()
            try:
                await asyncio.wait_for(self._wake_event.wait(), timeout=1.0)
            except TimeoutError:
                continue

    async def _dispatch_batch(self) -> None:
        now = datetime.now(UTC)
        async with self._sessions() as session, session.begin():
            await session.execute(
                text(
                    """
                    UPDATE device_commands
                    SET status = 'timed_out', updated_at = :now
                    WHERE status IN ('queued', 'published', 'accepted')
                      AND expires_at <= :now
                    """
                ),
                {"now": now},
            )
            await session.execute(
                text(
                    """
                    UPDATE device_commands
                    SET status = CASE
                            WHEN retry_count < :max_retries THEN 'queued'::command_status
                            ELSE 'timed_out'::command_status
                        END,
                        next_attempt_at = CASE
                            WHEN retry_count < :max_retries THEN :now
                            ELSE next_attempt_at
                        END,
                        updated_at = :now
                    WHERE status = 'published'
                      AND published_at <= :retry_before
                      AND expires_at > :now
                    """
                ),
                {
                    "now": now,
                    "retry_before": now - self._ack_timeout,
                    "max_retries": self._max_retries,
                },
            )
            result = await session.execute(
                text(
                    """
                    SELECT c.id, c.decision_id, c.classroom_id, c.device_id,
                           c.action, c.value, c.idempotency_key, c.expires_at,
                           cl.site_id
                    FROM device_commands AS c
                    JOIN classrooms AS cl ON cl.id = c.classroom_id
                    WHERE c.status = 'queued'
                      AND c.expires_at > :now
                      AND (c.next_attempt_at IS NULL OR c.next_attempt_at <= :now)
                    ORDER BY c.created_at
                    LIMIT 10
                    FOR UPDATE OF c SKIP LOCKED
                    """
                ),
                {"now": now},
            )
            commands = [dict(row) for row in result.mappings().all()]
            if commands:
                ids = [command["id"] for command in commands]
                await session.execute(
                    text(
                        """
                        UPDATE device_commands
                        SET retry_count = retry_count + 1,
                            next_attempt_at = :retry_at
                        WHERE id = ANY(CAST(:ids AS uuid[])) AND status = 'queued'
                        """
                    ),
                    {"ids": ids, "retry_at": now + timedelta(seconds=5)},
                )

        for command in commands:
            await self._publish(command)

    async def _publish(self, command: dict[str, Any]) -> None:
        topic = (
            f"{self._topic_root}/{command['site_id']}/{command['classroom_id']}"
            f"/command/{command['device_id']}/set"
        )
        envelope = {
            "schema_version": "1.0",
            "message_id": str(uuid4()),
            "occurred_at": _format_utc(datetime.now(UTC)),
            "site_id": str(command["site_id"]),
            "classroom_id": str(command["classroom_id"]),
            "producer_id": self._producer_id,
            "payload": {
                "command_id": str(command["id"]),
                "decision_id": str(command["decision_id"]),
                "device_id": str(command["device_id"]),
                "action": command["action"],
                "value": command["value"],
                "expires_at": _format_utc(command["expires_at"]),
                "idempotency_key": command["idempotency_key"],
            },
        }
        info = self._client.publish(
            topic,
            json.dumps(envelope, separators=(",", ":")),
            qos=1,
            retain=False,
        )
        if info.rc != mqtt.MQTT_ERR_SUCCESS:
            logger.warning("MQTT command publish was not queued: command_id=%s", command["id"])
            return
        try:
            await asyncio.to_thread(info.wait_for_publish, 3.0)
        except (RuntimeError, ValueError):
            logger.warning("MQTT command publish did not complete: command_id=%s", command["id"])
            return
        if not info.is_published():
            logger.warning("MQTT command publish is unconfirmed: command_id=%s", command["id"])
            return

        async with self._sessions() as session:
            await session.execute(
                text(
                    """
                    UPDATE device_commands
                    SET status = 'published', published_at = :published_at,
                        updated_at = :published_at
                    WHERE id = :command_id AND status = 'queued'
                    """
                ),
                {"command_id": command["id"], "published_at": datetime.now(UTC)},
            )
            await session.commit()
        logger.info("Published approved MQTT command: command_id=%s", command["id"])

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
            logger.error("MQTT dispatcher connection rejected: %s", reason_code)
            return
        self.connected = True
        self.wake()
        logger.info("MQTT command dispatcher connected")

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
            logger.warning("MQTT command dispatcher disconnected: %s; reconnecting", reason_code)


def _format_utc(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
