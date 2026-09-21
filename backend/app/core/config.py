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

    model_config = SettingsConfigDict(
        env_file=Path(__file__).resolve().parents[2] / ".env",
        extra="ignore",
        populate_by_name=True,
    )
