"""FastAPI application entrypoint for the local telemetry and control backend."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import UUID

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from app.api.v1.routers.device_control import router as device_control_router
from app.api.v1.routers.environment_analysis import router as environment_analysis_router
from app.core.config import Settings
from app.core.db import create_database
from app.integrations.mqtt.dispatcher import MqttCommandDispatcher
from app.integrations.mqtt.ingestion import EnvironmentEnvelope, MqttIngestion
from app.repositories.device_control import (
    persist_command_acknowledgement as store_command_acknowledgement,
)
from app.repositories.device_control import (
    persist_device_state as store_device_state,
)
from app.repositories.device_control import (
    persist_gateway_availability as store_gateway_availability,
)
from app.repositories.environment import insert_environment_reading
from app.repositories.occupancy import insert_occupancy_observation
from app.schemas.mqtt import (
    AvailabilityPayload,
    CommandAcknowledgementPayload,
    DeviceStatePayload,
    MqttEnvelope,
    OccupancyObservationPayload,
)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = Settings()
    engine, sessions = create_database(settings.database_url)

    async def persist_environment(envelope: EnvironmentEnvelope) -> bool:
        async with sessions() as session:
            return await insert_environment_reading(session, envelope)

    async def persist_occupancy(
        envelope: MqttEnvelope[OccupancyObservationPayload],
    ) -> bool:
        async with sessions() as session:
            return await insert_occupancy_observation(session, envelope)

    async def persist_device_state(
        envelope: MqttEnvelope[DeviceStatePayload],
        _topic_device_id: UUID,
        retained: bool,
    ) -> None:
        async with sessions() as session:
            await store_device_state(
                session,
                site_id=envelope.site_id,
                classroom_id=envelope.classroom_id,
                message_id=envelope.message_id,
                retained=retained,
                occurred_at=envelope.occurred_at,
                payload=envelope.payload,
            )

    async def persist_gateway_availability(
        envelope: MqttEnvelope[AvailabilityPayload],
    ) -> None:
        async with sessions() as session:
            await store_gateway_availability(
                session,
                site_id=envelope.site_id,
                classroom_id=envelope.classroom_id,
                occurred_at=envelope.occurred_at,
                payload=envelope.payload,
            )

    async def persist_acknowledgement(
        envelope: MqttEnvelope[CommandAcknowledgementPayload],
    ) -> None:
        async with sessions() as session:
            await store_command_acknowledgement(
                session,
                site_id=envelope.site_id,
                classroom_id=envelope.classroom_id,
                message_id=envelope.message_id,
                occurred_at=envelope.occurred_at,
                payload=envelope.payload,
            )

    mqtt_ingestion = MqttIngestion(
        host=settings.mqtt_host,
        port=settings.mqtt_port,
        topic_root=settings.mqtt_topic_root,
        client_id=settings.mqtt_client_id,
        tls_enabled=settings.mqtt_tls_enabled,
        username=settings.mqtt_username,
        password=settings.mqtt_password,
        persist=persist_environment,
        persist_occupancy_observation=persist_occupancy,
        persist_device_state=persist_device_state,
        persist_gateway_availability=persist_gateway_availability,
        persist_acknowledgement=persist_acknowledgement,
    )
    command_dispatcher = MqttCommandDispatcher(
        host=settings.mqtt_host,
        port=settings.mqtt_port,
        topic_root=settings.mqtt_topic_root,
        client_id=settings.mqtt_dispatcher_client_id,
        producer_id=settings.mqtt_dispatcher_producer_id,
        sessions=sessions,
        tls_enabled=settings.mqtt_tls_enabled,
        username=settings.mqtt_dispatcher_username,
        password=settings.mqtt_dispatcher_password,
        ack_timeout_seconds=settings.mqtt_command_ack_timeout_seconds,
        max_retries=settings.mqtt_command_max_retries,
    )
    app.state.engine = engine
    app.state.sessions = sessions
    app.state.mqtt = mqtt_ingestion
    app.state.command_dispatcher = command_dispatcher
    app.state.local_hardware_control_enabled = settings.local_hardware_control_enabled
    try:
        await mqtt_ingestion.start()
        await command_dispatcher.start()
        yield
    finally:
        await command_dispatcher.stop()
        await mqtt_ingestion.stop()
        await engine.dispose()


app = FastAPI(title="AI Classroom Energy Optimizer", version="1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ],
    allow_credentials=True,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type", "Idempotency-Key"],
)
app.include_router(environment_analysis_router)
app.include_router(device_control_router)


@app.get("/health/live")
async def live() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/health/ready")
async def ready(request: Request) -> dict[str, str]:
    async with request.app.state.engine.connect() as connection:
        await connection.execute(text("SELECT 1"))
    if not request.app.state.mqtt.connected:
        return {"status": "degraded", "mqtt": "disconnected"}
    if not request.app.state.command_dispatcher.connected:
        return {"status": "degraded", "mqtt": "connected", "dispatcher": "disconnected"}
    return {"status": "ok", "mqtt": "connected", "dispatcher": "connected"}
