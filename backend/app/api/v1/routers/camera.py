"""Authorized, ephemeral camera previews; no video is recorded."""

import asyncio
import time
from collections.abc import AsyncIterator
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import StreamingResponse

from app.core.config import Settings
from app.core.private_bridge import require_monitoring_access
from app.services.camera_preview import (
    CameraFrameTimeout,
    CameraPreview,
    CameraUnavailable,
    PreviewFrame,
)

router = APIRouter(prefix="/api/v1/classrooms", tags=["camera preview"])
NO_STORE = {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"}
STREAM_BOUNDARY = "classroom-frame"
STREAM_SECONDS = 60.0


def _camera_preview(classroom_id: UUID, request: Request) -> CameraPreview:
    require_monitoring_access(request)
    if request.url.query:
        raise HTTPException(
            400, "Camera preview does not accept query parameters.", headers=NO_STORE
        )
    settings = getattr(request.app.state, "analytics_settings", None)
    if (
        not isinstance(settings, Settings)
        or classroom_id != settings.classroom_api_proxy_classroom_id
    ):
        raise HTTPException(404, "Camera preview not found.", headers=NO_STORE)
    preview: CameraPreview | None = getattr(request.app.state, "camera_preview", None)
    if preview is None:
        raise HTTPException(503, "Camera preview is not available.", headers=NO_STORE)
    return preview


async def _first_frame(preview: CameraPreview) -> PreviewFrame:
    try:
        frame = await preview.get_frame()
    except CameraUnavailable:
        raise HTTPException(503, "Camera preview is not available.", headers=NO_STORE) from None
    except CameraFrameTimeout:
        raise HTTPException(
            504, "A fresh camera frame could not be received.", headers=NO_STORE
        ) from None
    return frame


@router.get("/{classroom_id}/camera/frame", responses={200: {"content": {"image/jpeg": {}}}})
async def read_camera_frame(classroom_id: UUID, request: Request) -> Response:
    preview = _camera_preview(classroom_id, request)
    frame = await _first_frame(preview)
    age_ms = max(0, int((time.monotonic() - frame.received_at) * 1000))
    return Response(
        content=frame.jpeg,
        media_type="image/jpeg",
        headers={**NO_STORE, "X-Camera-Frame-Age-Ms": str(age_ms)},
    )


async def _stream_parts(preview: CameraPreview, first: PreviewFrame) -> AsyncIterator[bytes]:
    """Send only new frames under backpressure; renew viewer auth every minute."""
    deadline = time.monotonic() + STREAM_SECONDS
    frame = first
    while True:
        yield (
            f"--{STREAM_BOUNDARY}\r\nContent-Type: image/jpeg\r\n"
            f"Content-Length: {len(frame.jpeg)}\r\n\r\n"
        ).encode("ascii") + frame.jpeg + b"\r\n"
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        try:
            async with asyncio.timeout(remaining):
                frame = await preview.get_frame(after_received_at=frame.received_at)
        except (CameraUnavailable, CameraFrameTimeout, TimeoutError):
            break
    yield f"--{STREAM_BOUNDARY}--\r\n".encode("ascii")


@router.get(
    "/{classroom_id}/camera/stream",
    responses={200: {"content": {"multipart/x-mixed-replace": {}}}},
)
async def read_camera_stream(classroom_id: UUID, request: Request) -> StreamingResponse:
    preview = _camera_preview(classroom_id, request)
    # Failures before the first JPEG retain ordinary, generic JSON status codes.
    # StreamingResponse cancels this generator when its client disconnects.
    frame = await _first_frame(preview)
    return StreamingResponse(
        _stream_parts(preview, frame),
        media_type=f"multipart/x-mixed-replace; boundary={STREAM_BOUNDARY}",
        headers={**NO_STORE, "Cache-Control": "no-store, no-transform"},
    )
