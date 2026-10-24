from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from uuid import uuid4

import paho.mqtt.client as mqtt

logger = logging.getLogger("cv_service.mqtt")


def _utc_now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


class OccupancyPublisher:
    """Publish aggregate occupancy observations to the classroom MQTT topic."""

    def __init__(
        self,
        *,
        host: str,
        port: int,
        topic_root: str,
        client_id: str,
        site_id: str,
        classroom_id: str,
        producer_id: str,
        camera_id: str,
        username: str | None = None,
        password: str | None = None,
        tls_enabled: bool = False,
    ) -> None:
        self._host = host
        self._port = port
        self._topic_root = topic_root.rstrip("/")
        self._site_id = site_id
        self._classroom_id = classroom_id
        self._producer_id = producer_id
        self._camera_id = camera_id
        self._client = mqtt.Client(client_id=client_id)
        if username is not None:
            self._client.username_pw_set(username, password)
        if tls_enabled:
            self._client.tls_set()
        self._client.on_connect = self._on_connect
        self._client.on_disconnect = self._on_disconnect
        self.connected = False

    def _on_connect(
        self,
        _client: mqtt.Client,
        _userdata: object,
        _flags: object,
        result_code: int,
    ) -> None:
        if result_code != mqtt.MQTT_ERR_SUCCESS:
            self.connected = False
            logger.warning("MQTT connection rejected: %s", result_code)
            return
        self.connected = True
        logger.info("MQTT occupancy publisher connected: %s:%s", self._host, self._port)

    def _on_disconnect(
        self,
        _client: mqtt.Client,
        _userdata: object,
        result_code: int,
    ) -> None:
        self.connected = False
        if result_code != mqtt.MQTT_ERR_SUCCESS:
            logger.warning("MQTT occupancy publisher disconnected: %s", result_code)

    def connect(self) -> bool:
        try:
            self._client.connect(self._host, self._port, keepalive=30)
            self._client.loop_start()
            return True
        except Exception:
            logger.exception("MQTT occupancy publisher failed to connect")
            return False

    def publish_observation(
        self,
        *,
        count: int,
        confidence: float,
        frame_sequence: int,
        source: str = "roboflow_bytetrack",
    ) -> bool:
        if count < 0:
            logger.warning("Ignoring negative occupancy count for publish: %s", count)
            return False

        topic = (
            f"{self._topic_root}/{self._site_id}/{self._classroom_id}/occupancy/observation"
        )
        envelope = {
            "schema_version": "1.0",
            "message_id": str(uuid4()),
            "occurred_at": _utc_now_iso(),
            "site_id": self._site_id,
            "classroom_id": self._classroom_id,
            "producer_id": self._producer_id,
            "payload": {
                "camera_id": self._camera_id,
                "count": count,
                "confidence": max(0.0, min(1.0, float(confidence))),
                "entries_delta": 0,
                "exits_delta": 0,
                "frame_sequence": int(frame_sequence),
                "source": source,
            },
        }
        try:
            info = self._client.publish(
                topic,
                json.dumps(envelope, separators=(",", ":")),
                qos=1,
                retain=False,
            )
            if info.rc != mqtt.MQTT_ERR_SUCCESS:
                logger.warning("MQTT publish failed with rc=%s on topic=%s", info.rc, topic)
                return False
            try:
                info.wait_for_publish(3.0)
            except Exception:
                logger.warning("MQTT publish confirmation timed out for topic=%s", topic)
                return False
            logger.debug(
                "Published occupancy observation count=%s frame_sequence=%s",
                count,
                frame_sequence,
            )
            return True
        except Exception:
            logger.exception("Failed to publish occupancy observation on topic=%s", topic)
            return False
