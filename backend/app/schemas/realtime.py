"""Versioned WebSocket message contracts."""

from __future__ import annotations

from enum import Enum
from typing import Generic, TypeVar
from uuid import UUID

from pydantic import Field

from .common import ContractModel, JsonObject, UtcDateTime

DataT = TypeVar("DataT")


class ServerEventType(str, Enum):
    CONNECTION_READY = "connection.ready"
    SNAPSHOT_UPDATED = "snapshot.updated"
    OCCUPANCY_UPDATED = "occupancy.updated"
    ENVIRONMENT_UPDATED = "environment.updated"
    DEVICE_STATE_UPDATED = "device.state_updated"
    ENERGY_UPDATED = "energy.updated"
    PREDICTION_UPDATED = "prediction.updated"
    RECOMMENDATION_CREATED = "recommendation.created"
    CONTROL_DECISION = "control.decision"
    COMMAND_STATUS = "command.status"
    OVERRIDE_UPDATED = "override.updated"
    SCHEDULE_UPDATED = "schedule.updated"
    ALERT_CREATED = "alert.created"
    HEARTBEAT = "heartbeat"
    ERROR = "error"


class ClientEventType(str, Enum):
    SNAPSHOT_REQUEST = "snapshot.request"
    STREAM_CONFIGURE = "stream.configure"
    PING = "ping"


class WebSocketServerMessage(ContractModel, Generic[DataT]):
    schema_version: str = Field(pattern=r"^1\.[0-9]+$")
    event_id: UUID
    type: ServerEventType
    occurred_at: UtcDateTime
    classroom_id: UUID
    sequence: int = Field(ge=0)
    data: DataT


class WebSocketClientMessage(ContractModel, Generic[DataT]):
    schema_version: str = Field(pattern=r"^1\.[0-9]+$")
    request_id: UUID
    type: ClientEventType
    data: DataT


class ConnectionReadyData(ContractModel):
    connection_id: UUID
    heartbeat_seconds: int = Field(gt=0)


class StreamConfigureData(ContractModel):
    streams: list[str]


class HeartbeatData(ContractModel):
    server_time: UtcDateTime


class PingData(ContractModel):
    client_time: UtcDateTime | None = None


class WebSocketErrorData(ContractModel):
    code: str
    message: str
    request_id: UUID | None = None
    details: JsonObject = Field(default_factory=dict)

