"""Read-only snapshots and live updates for the two ESP32 prototypes."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request, Response, status
from fastapi.responses import StreamingResponse
from sqlalchemy.exc import SQLAlchemyError

from app.core.private_bridge import require_monitoring_access
from app.repositories.solar_monitor import read_prototype_snapshot
from app.schemas.solar_monitor import PrototypeSnapshot

router = APIRouter(prefix="/api/v1/classrooms", tags=["prototype monitoring"])
logger = logging.getLogger(__name__)
STREAM_SECONDS = 60
STREAM_INTERVAL_SECONDS = 1


async def _snapshot(
    classroom_id: UUID, request: Request, *, include_history: bool
) -> PrototypeSnapshot:
    state = request.app.state
    try:
        async with state.sessions() as session:
            snapshot = await read_prototype_snapshot(
                session,
                classroom_id,
                state.analytics_settings,
                broker_connected=state.mqtt.connected,
                broker_connected_at=state.mqtt.connected_at,
                include_history=include_history,
            )
    except (SQLAlchemyError, OSError) as exc:
        logger.warning("Prototype monitor database unavailable (%s)", type(exc).__name__)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Prototype monitoring storage is unavailable.",
        ) from exc
    if snapshot is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Classroom was not found.",
        )
    return snapshot


@router.get("/{classroom_id}/prototype-monitor", response_model=PrototypeSnapshot)
async def read_monitor(
    classroom_id: UUID, request: Request, response: Response
) -> PrototypeSnapshot:
    require_monitoring_access(request)
    response.headers["Cache-Control"] = "private, no-store"
    return await _snapshot(classroom_id, request, include_history=True)


@router.get("/{classroom_id}/prototype-monitor/stream")
async def stream_monitor(classroom_id: UUID, request: Request) -> StreamingResponse:
    require_monitoring_access(request)
    snapshot = await _snapshot(classroom_id, request, include_history=False)

    async def events() -> AsyncIterator[str]:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + STREAM_SECONDS
        current = snapshot
        while True:
            serialized = current.model_dump_json(
                exclude={"history", "energy_history", "classroom_meter_history"}
            )
            yield f"event: snapshot\ndata: {serialized}\n\n"
            if loop.time() + STREAM_INTERVAL_SECONDS >= deadline:
                break
            await asyncio.sleep(STREAM_INTERVAL_SECONDS)
            if await request.is_disconnected():
                break
            try:
                current = await _snapshot(classroom_id, request, include_history=False)
            except HTTPException:
                break

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "private, no-store", "X-Accel-Buffering": "no"},
    )
