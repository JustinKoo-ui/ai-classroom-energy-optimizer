"""Loopback-only manual control endpoints for the local hardware prototype."""

from __future__ import annotations

import ipaddress
from datetime import UTC, datetime, timedelta
from uuid import UUID

from fastapi import APIRouter, Header, HTTPException, Request, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.private_bridge import is_private_bridge_request, require_remote_control
from app.domain.local_hardware import (
    LOCAL_CLASSROOM_ID,
    LOCAL_RELAY_DEVICE_IDS,
    LOCAL_SITE_ID,
)
from app.schemas.device_control import (
    DeviceCommandStatus,
    DeviceControlState,
    DeviceControlStateList,
    ManualCommandRequest,
    ManualCommandResponse,
    UrgencyStatus,
)
from app.services.device_control import (
    DeviceNotFoundError,
    IdempotencyConflictError,
    submit_manual_command,
)
from app.services.occupancy_automation import (
    UrgencyUnavailableError,
    get_local_urgency_status,
    start_local_urgency,
)

router = APIRouter(prefix="/api/v1", tags=["local device control"])


def _require_loopback(request: Request) -> None:
    if is_private_bridge_request(request):
        require_remote_control(request)
        if not getattr(request.app.state, "local_hardware_control_enabled", False):
            raise HTTPException(status_code=404, detail="Local hardware control is disabled.")
        return
    if not getattr(request.app.state, "local_hardware_control_enabled", False):
        raise HTTPException(status_code=404, detail="Local hardware control is disabled.")
    client_host = request.client.host if request.client is not None else ""
    try:
        allowed = ipaddress.ip_address(client_host).is_loopback
    except ValueError:
        allowed = False
    if not allowed:
        raise HTTPException(status_code=403, detail="Device control is localhost-only.")


def _get_sessions(request: Request) -> async_sessionmaker[AsyncSession]:
    sessions: async_sessionmaker[AsyncSession] | None = getattr(
        request.app.state,
        "sessions",
        None,
    )
    if sessions is None:
        raise HTTPException(status_code=503, detail="Database is not available.")
    return sessions


@router.get(
    "/classrooms/{classroom_id}/devices",
    response_model=DeviceControlStateList,
)
async def list_device_states(classroom_id: UUID, request: Request) -> DeviceControlStateList:
    _require_loopback(request)
    if is_private_bridge_request(request) and classroom_id != LOCAL_CLASSROOM_ID:
        raise HTTPException(status_code=404, detail="Classroom was not found.")
    now = datetime.now(UTC)
    sessions = _get_sessions(request)
    async with sessions() as session:
        result = await session.execute(
            text(
                """
                SELECT d.id, d.name, d.type, ds.operational_status,
                       ds.reported_state, ds.fault_codes, ds.state_version,
                       ds.last_seen_at, ds.reported_at,
                       pending.id AS pending_command_id,
                       pending.status AS pending_command_status
                FROM devices AS d
                JOIN classrooms AS classroom ON classroom.id = d.classroom_id
                LEFT JOIN device_states AS ds ON ds.device_id = d.id
                LEFT JOIN LATERAL (
                    SELECT id, status
                    FROM device_commands
                    WHERE device_id = d.id AND status IN ('queued', 'published', 'accepted')
                    ORDER BY created_at DESC
                    LIMIT 1
                ) AS pending ON true
                WHERE d.classroom_id = :classroom_id
                  AND classroom.site_id = :site_id
                  AND d.hardware_uid LIKE 'aceo-local-relay-%'
                  AND d.id = ANY(CAST(:device_ids AS uuid[]))
                ORDER BY d.name
                """
            ),
            {
                "classroom_id": classroom_id,
                "site_id": LOCAL_SITE_ID,
                "device_ids": list(LOCAL_RELAY_DEVICE_IDS),
            },
        )
        rows = result.mappings().all()

    devices = []
    for row in rows:
        last_seen = row.last_seen_at
        freshness = (
            "unknown"
            if last_seen is None
            else "fresh"
            if now - last_seen <= timedelta(seconds=15)
            else "stale"
        )
        devices.append(
            DeviceControlState(
                device_id=row.id,
                name=row.name,
                type=row.type,
                operational_status=row.operational_status or "registered",
                reported_state=row.reported_state or {},
                fault_codes=list(row.fault_codes or []),
                state_version=row.state_version or 0,
                last_seen_at=last_seen,
                reported_at=row.reported_at,
                freshness=freshness,
                pending_command_id=row.pending_command_id,
                pending_command_status=row.pending_command_status,
            )
        )
    return DeviceControlStateList(classroom_id=classroom_id, devices=devices)


@router.post(
    "/devices/{device_id}/commands",
    response_model=ManualCommandResponse,
)
async def submit_device_command(
    device_id: UUID,
    body: ManualCommandRequest,
    request: Request,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=1, max_length=200),
) -> ManualCommandResponse:
    _require_loopback(request)
    try:
        submission = await submit_manual_command(
            _get_sessions(request),
            device_id=device_id,
            idempotency_key=idempotency_key,
            request=body,
            operator_email=getattr(request.state, "remote_operator_email", None),
        )
    except DeviceNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Device is not registered.") from exc
    except IdempotencyConflictError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Idempotency-Key was already used for a different request.",
        ) from exc
    dispatcher = getattr(request.app.state, "command_dispatcher", None)
    if submission.response.command_status is not None and dispatcher is not None:
        dispatcher.wake()
    return submission.response


@router.get("/commands/{command_id}", response_model=DeviceCommandStatus)
async def read_command_status(command_id: UUID, request: Request) -> DeviceCommandStatus:
    _require_loopback(request)
    sessions = _get_sessions(request)
    async with sessions() as session:
        result = await session.execute(
            text(
                """
                SELECT c.id AS command_id, c.device_id, c.decision_id, c.status,
                       c.created_at, c.updated_at, event.reason_code,
                       event.reported_state
                FROM device_commands AS c
                JOIN devices AS d ON d.id = c.device_id
                JOIN classrooms AS classroom ON classroom.id = d.classroom_id
                LEFT JOIN LATERAL (
                    SELECT reason_code, reported_state
                    FROM command_events
                    WHERE command_id = c.id
                    ORDER BY occurred_at DESC, received_at DESC
                    LIMIT 1
                ) AS event ON true
                WHERE c.id = :command_id
                  AND d.hardware_uid LIKE 'aceo-local-relay-%'
                  AND d.id = ANY(CAST(:device_ids AS uuid[]))
                  AND d.classroom_id = :classroom_id
                  AND classroom.site_id = :site_id
                """
            ),
            {
                "command_id": command_id,
                "device_ids": list(LOCAL_RELAY_DEVICE_IDS),
                "classroom_id": LOCAL_CLASSROOM_ID,
                "site_id": LOCAL_SITE_ID,
            },
        )
        row = result.mappings().one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Command was not found.")
    return DeviceCommandStatus(
        command_id=row.command_id,
        device_id=row.device_id,
        decision_id=row.decision_id,
        status=row.status,
        reason_code=row.reason_code,
        reported_state=row.reported_state,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


@router.get(
    "/classrooms/{classroom_id}/urgency",
    response_model=UrgencyStatus,
)
async def read_urgency_status(classroom_id: UUID, request: Request) -> UrgencyStatus:
    _require_loopback(request)
    if classroom_id != LOCAL_CLASSROOM_ID:
        raise HTTPException(status_code=404, detail="Classroom was not found.")
    result = await get_local_urgency_status(_get_sessions(request))
    return UrgencyStatus(**result)


@router.post(
    "/classrooms/{classroom_id}/urgency",
    response_model=UrgencyStatus,
    status_code=status.HTTP_202_ACCEPTED,
)
async def activate_urgency(classroom_id: UUID, request: Request) -> UrgencyStatus:
    _require_loopback(request)
    if classroom_id != LOCAL_CLASSROOM_ID:
        raise HTTPException(status_code=404, detail="Classroom was not found.")
    try:
        result = await start_local_urgency(_get_sessions(request))
    except UrgencyUnavailableError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    dispatcher = getattr(request.app.state, "command_dispatcher", None)
    if dispatcher is not None:
        dispatcher.wake()
    return UrgencyStatus(**result)
