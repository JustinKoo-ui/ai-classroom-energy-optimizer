"""Authenticate the narrow server-to-server monitoring and relay bridge.

The browser never receives this token. Public user authentication belongs to
the Worker; this separate secret authenticates that Worker's private request.
"""

from __future__ import annotations

import ipaddress
import secrets
from contextlib import suppress
from uuid import UUID

from fastapi import HTTPException, Request, status
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from app.core.config import Settings
from app.domain.local_hardware import LOCAL_CLASSROOM_ID, LOCAL_RELAY_DEVICE_IDS

PROXY_TOKEN_HEADER = "x-classroom-proxy-token"
OPERATOR_EMAIL_HEADER = "x-classroom-operator-email"
_DENIED = "Private monitoring bridge access denied."


def is_private_bridge_request(request: Request) -> bool:
    """Even an explicitly empty header invokes bridge authentication."""
    return PROXY_TOKEN_HEADER in request.headers or OPERATOR_EMAIL_HEADER in request.headers


def require_private_bridge(request: Request) -> str | None:
    """Deny on missing configuration, wrong token, path, classroom or method."""
    settings = getattr(request.app.state, "analytics_settings", None)
    if not isinstance(settings, Settings) or not settings.classroom_api_proxy_enabled:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=_DENIED)

    expected = settings.classroom_api_proxy_token
    values = request.headers.getlist(PROXY_TOKEN_HEADER)
    if (
        expected is None
        or len(expected.get_secret_value().strip()) < 32
        or len(values) != 1
        or not secrets.compare_digest(values[0].encode(), expected.get_secret_value().encode())
    ):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=_DENIED)

    base = f"/api/v1/classrooms/{settings.classroom_api_proxy_classroom_id}"
    allowed = {
        ("GET", f"{base}/telemetry/latest"),
        ("GET", f"{base}/occupancy/current"),
        ("GET", f"{base}/camera/frame"),
        ("GET", f"{base}/camera/stream"),
        ("GET", f"{base}/analytics"),
        ("POST", f"{base}/analytics/report"),
    }
    if (request.method, request.url.path) in allowed:
        return None
    return _require_remote_control(request, settings)


def require_remote_control(request: Request) -> str:
    """Always authenticate the bridge before authorizing a remote operator."""
    operator = require_private_bridge(request)
    if operator is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=_DENIED)
    return operator


def _require_remote_control(request: Request, settings: Settings) -> str:
    """Apply the operator and route gates after bridge credentials validate."""
    if (
        not settings.classroom_remote_control_enabled
        or settings.classroom_api_proxy_classroom_id != LOCAL_CLASSROOM_ID
    ):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=_DENIED)
    values = request.headers.getlist(OPERATOR_EMAIL_HEADER)
    if len(values) != 1:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=_DENIED)
    email = values[0].strip().casefold()
    allowed_operators = {
        value.strip().casefold()
        for value in settings.classroom_remote_control_allowed_emails.split(",")
        if value.strip()
    }
    if not email or email not in allowed_operators:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=_DENIED)

    base = f"/api/v1/classrooms/{LOCAL_CLASSROOM_ID}"
    allowed_paths = {
        ("GET", f"{base}/devices"),
        ("GET", f"{base}/urgency"),
        *{
            ("POST", f"/api/v1/devices/{device_id}/commands")
            for device_id in LOCAL_RELAY_DEVICE_IDS
        },
    }
    path = request.url.path
    command_status = False
    if request.method == "GET" and path.startswith("/api/v1/commands/"):
        candidate = path.removeprefix("/api/v1/commands/")
        with suppress(ValueError):
            command_status = str(UUID(candidate)) == candidate
    if (request.method, path) not in allowed_paths and not command_status:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=_DENIED)
    request.state.remote_operator_email = email
    return email


def require_monitoring_access(request: Request) -> None:
    """Preserve direct local access; an explicit bridge header must always validate."""
    if is_private_bridge_request(request):
        require_private_bridge(request)
        return

    client_host = request.client.host if request.client is not None else ""
    try:
        is_local_client = ipaddress.ip_address(client_host).is_loopback
    except ValueError:
        is_local_client = False
    if not is_local_client:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Remote monitoring requires the authenticated private bridge.",
        )


class PrivateBridgeMiddleware:
    """Reject bridge credentials outside the configured route scope before dispatch."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            request = Request(scope)
            if is_private_bridge_request(request):
                try:
                    require_private_bridge(request)
                except HTTPException as exc:
                    response = JSONResponse({"detail": exc.detail}, status_code=exc.status_code)
                    await response(scope, receive, send)
                    return
        await self.app(scope, receive, send)
