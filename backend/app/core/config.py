"""Runtime configuration for the backend process."""

from __future__ import annotations

from pathlib import Path
from uuid import UUID

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.domain.local_hardware import LOCAL_CLASSROOM_ID


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
    camera_preview_enabled: bool = Field(default=False, alias="CAMERA_PREVIEW_ENABLED")
    camera_source: SecretStr | None = Field(default=None, alias="CAMERA_SOURCE")
    classroom_api_proxy_enabled: bool = Field(
        default=False,
        alias="CLASSROOM_API_PROXY_ENABLED",
    )
    classroom_api_proxy_token: SecretStr | None = Field(
        default=None,
        alias="CLASSROOM_API_PROXY_TOKEN",
    )
    classroom_api_proxy_classroom_id: UUID = Field(
        default=LOCAL_CLASSROOM_ID,
        alias="CLASSROOM_API_PROXY_CLASSROOM_ID",
    )
    classroom_remote_control_enabled: bool = Field(
        default=False,
        alias="CLASSROOM_REMOTE_CONTROL_ENABLED",
    )
    classroom_remote_control_allowed_emails: str = Field(
        default="",
        alias="CLASSROOM_REMOTE_CONTROL_ALLOWED_EMAILS",
        max_length=4000,
    )
    analytics_stale_after_seconds: int = Field(
        default=15,
        alias="ANALYTICS_STALE_AFTER_SECONDS",
        ge=2,
        le=300,
    )
    analytics_light_power_w: float = Field(
        default=10,
        alias="ANALYTICS_LIGHT_POWER_W",
        ge=0,
        le=100_000,
        allow_inf_nan=False,
    )
    analytics_fan_power_w: float = Field(
        default=60,
        alias="ANALYTICS_FAN_POWER_W",
        ge=0,
        le=100_000,
        allow_inf_nan=False,
    )
    analytics_ac_power_w: float = Field(
        default=1000,
        alias="ANALYTICS_AC_POWER_W",
        ge=0,
        le=100_000,
        allow_inf_nan=False,
    )
    analyst_provider: str = Field(default="openai_compatible", alias="ANALYST_PROVIDER")
    analyst_api_url: str = Field(
        default="https://api.openai.com/v1/chat/completions",
        alias="ANALYST_API_URL",
    )
    analyst_api_key: SecretStr | None = Field(default=None, alias="ANALYST_API_KEY")
    analyst_model: str = Field(default="", alias="ANALYST_MODEL", max_length=200)
    analyst_access_token: SecretStr | None = Field(default=None, alias="ANALYST_ACCESS_TOKEN")
    analyst_timeout_seconds: float = Field(
        default=20,
        alias="ANALYST_TIMEOUT_SECONDS",
        ge=1,
        le=60,
    )
    analyst_max_retries: int = Field(default=1, alias="ANALYST_MAX_RETRIES", ge=0, le=2)
    analyst_requests_per_minute: int = Field(
        default=3,
        alias="ANALYST_REQUESTS_PER_MINUTE",
        ge=1,
        le=30,
    )
    analyst_cache_ttl_seconds: int = Field(
        default=300,
        alias="ANALYST_CACHE_TTL_SECONDS",
        ge=0,
        le=3600,
    )

    model_config = SettingsConfigDict(
        env_file=Path(__file__).resolve().parents[2] / ".env",
        extra="ignore",
        populate_by_name=True,
    )
