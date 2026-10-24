"""Runtime configuration for the backend process."""

from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str = Field(alias="DATABASE_URL")
    mqtt_host: str = Field(default="localhost", alias="MQTT_HOST")
    mqtt_port: int = Field(default=1883, alias="MQTT_PORT")
    mqtt_tls_enabled: bool = Field(default=False, alias="MQTT_TLS_ENABLED")
    mqtt_topic_root: str = Field(default="aceo/v1", alias="MQTT_TOPIC_ROOT")
    mqtt_client_id: str = Field(default="aceo-backend-ingestion", alias="MQTT_CLIENT_ID")
    mqtt_username: str | None = Field(default=None, alias="MQTT_USERNAME")
    mqtt_password: str | None = Field(default=None, alias="MQTT_PASSWORD")
    mqtt_dispatcher_client_id: str = Field(
        default="aceo-backend-dispatcher",
        alias="MQTT_DISPATCHER_CLIENT_ID",
    )
    mqtt_dispatcher_producer_id: str = Field(
        default="command-dispatcher-local",
        alias="MQTT_DISPATCHER_PRODUCER_ID",
    )
    mqtt_dispatcher_username: str | None = Field(
        default=None,
        alias="MQTT_DISPATCHER_USERNAME",
    )
    mqtt_dispatcher_password: str | None = Field(
        default=None,
        alias="MQTT_DISPATCHER_PASSWORD",
    )
    mqtt_command_ack_timeout_seconds: int = Field(
        default=10,
        alias="MQTT_COMMAND_ACK_TIMEOUT_SECONDS",
        gt=0,
    )
    mqtt_command_max_retries: int = Field(
        default=3,
        alias="MQTT_COMMAND_MAX_RETRIES",
        ge=0,
    )
    local_hardware_control_enabled: bool = Field(
        default=False,
        alias="LOCAL_HARDWARE_CONTROL_ENABLED",
    )
    model_config = SettingsConfigDict(
        env_file=Path(__file__).resolve().parents[2] / ".env",
        extra="ignore",
        populate_by_name=True,
    )
