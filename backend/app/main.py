"""FastAPI application entrypoint for the local ingestion backend."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI, Request
from sqlalchemy import text

from app.core.config import Settings
from app.core.db import create_database
from app.integrations.mqtt.ingestion import MqttIngestion
from app.repositories.environment import insert_environment_reading


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = Settings()
    engine, sessions = create_database(settings.database_url)

    async def persist(envelope):
        async with sessions() as session:
            return await insert_environment_reading(session, envelope)

    mqtt_ingestion = MqttIngestion(
        host=settings.mqtt_host,
        port=settings.mqtt_port,
        topic_root=settings.mqtt_topic_root,
        client_id=settings.mqtt_client_id,
        tls_enabled=settings.mqtt_tls_enabled,
        username=settings.mqtt_username,
        password=settings.mqtt_password,
        persist=persist,
    )
    app.state.engine = engine
    app.state.mqtt = mqtt_ingestion
    try:
        await mqtt_ingestion.start()
        yield
    finally:
        await mqtt_ingestion.stop()
        await engine.dispose()


app = FastAPI(title="AI Classroom Energy Optimizer", version="1.0", lifespan=lifespan)


@app.get("/health/live")
async def live() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/health/ready")
async def ready(request: Request) -> dict[str, str]:
    async with request.app.state.engine.connect() as connection:
        await connection.execute(text("SELECT 1"))
    if not request.app.state.mqtt.connected:
        return {"status": "degraded", "mqtt": "disconnected"}
    return {"status": "ok", "mqtt": "connected"}
