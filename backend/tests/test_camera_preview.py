"""Camera preview boundary/lifecycle tests; no network or physical camera."""

import asyncio
import queue
import subprocess
import time
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI
from pydantic import SecretStr

from app.api.v1.routers import camera as camera_routes
from app.api.v1.routers.camera import router
from app.core.config import Settings
from app.core.private_bridge import PROXY_TOKEN_HEADER, PrivateBridgeMiddleware
from app.domain.local_hardware import LOCAL_CLASSROOM_ID
from app.services.camera_preview import (
    CameraFrameTimeout,
    CameraPreview,
    CameraUnavailable,
    JpegParser,
    PreviewFrame,
)

SOURCE = "rtsp://admin:unit-test-camera-secret@192.0.2.10:554/stream1"
TOKEN = "unit-test-camera-bridge-secret-with-more-than-32-characters"
JPEG = b"\xff\xd8unit-test-frame\xff\xd9"
BASE = f"/api/v1/classrooms/{LOCAL_CLASSROOM_ID}/camera/frame"
STREAM = f"/api/v1/classrooms/{LOCAL_CLASSROOM_ID}/camera/stream"


def make_app(preview=None, **overrides):
    app = FastAPI()
    app.add_middleware(PrivateBridgeMiddleware)
    app.include_router(router)
    app.state.analytics_settings = Settings(_env_file=None, **{
        "database_url": "postgresql+asyncpg://unused",
        "classroom_api_proxy_enabled": True,
        "classroom_api_proxy_token": TOKEN,
        **overrides,
    })
    app.state.camera_preview = preview
    return app


async def request(app, path=BASE, host="127.0.0.1", headers=None, method="GET"):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, client=(host, 1234)), base_url="http://test"
    ) as client:
        return await client.request(method, path, headers=headers)


@pytest.mark.parametrize("host,headers", [
    ("127.0.0.1", {}),
    ("203.0.113.10", {PROXY_TOKEN_HEADER: TOKEN}),
])
async def test_local_and_authenticated_bridge_can_read_only_the_configured_camera(host, headers):
    preview = AsyncMock()
    preview.get_frame.return_value = PreviewFrame(JPEG, time.monotonic())
    response = await request(make_app(preview), host=host, headers=headers)
    assert response.status_code == 200
    assert response.content == JPEG
    assert response.headers["content-type"] == "image/jpeg"
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert int(response.headers["x-camera-frame-age-ms"]) >= 0
    assert SOURCE not in str(response.headers)


@pytest.mark.parametrize("host,headers", [
    ("203.0.113.10", {}),
    ("127.0.0.1", {PROXY_TOKEN_HEADER: "wrong"}),
    ("203.0.113.10", {PROXY_TOKEN_HEADER: "wrong"}),
])
async def test_camera_authentication_is_required_before_accessing_decoder(host, headers):
    preview = AsyncMock()
    response = await request(make_app(preview), host=host, headers=headers)
    assert response.status_code == 403
    preview.get_frame.assert_not_awaited()


@pytest.mark.parametrize("headers,status", [({}, 404), ({PROXY_TOKEN_HEADER: TOKEN}, 403)])
async def test_wrong_classroom_never_accesses_camera(headers, status):
    preview = AsyncMock()
    response = await request(make_app(preview),
                             f"/api/v1/classrooms/{uuid4()}/camera/frame", headers=headers)
    assert response.status_code == status
    preview.get_frame.assert_not_awaited()


async def test_camera_query_parameters_cannot_override_the_fixed_source():
    preview = AsyncMock()
    response = await request(make_app(preview), BASE + "?source=anything")
    assert response.status_code == 400
    assert response.headers["cache-control"] == "no-store"
    preview.get_frame.assert_not_awaited()


@pytest.mark.parametrize(
    "failure,status", [(CameraUnavailable(), 503), (CameraFrameTimeout(), 504)]
)
async def test_camera_errors_are_generic_and_uncached(failure, status):
    preview = AsyncMock()
    preview.get_frame.side_effect = failure
    response = await request(make_app(preview))
    assert response.status_code == status
    assert response.headers["cache-control"] == "no-store"
    assert "rtsp" not in response.text.lower()
    assert "secret" not in response.text.lower()


async def test_camera_disabled_by_default_and_missing_service_fails_closed():
    preview = CameraPreview(SecretStr(SOURCE))
    with pytest.raises(CameraUnavailable):
        await preview.get_frame()
    assert preview._runner is None
    assert (await request(make_app())).status_code == 503


@pytest.mark.parametrize("value", [None, "", "http://example.com/video", "file:///private.jpg"])
async def test_only_configured_rtsp_sources_are_opened(value):
    preview = CameraPreview(SecretStr(value) if value is not None else None, enabled=True)
    with pytest.raises(CameraUnavailable):
        await preview.get_frame()
    assert preview._runner is None


def test_jpeg_parser_handles_split_markers_multiple_frames_and_bounded_storage():
    parser = JpegParser(max_bytes=64)
    assert parser.feed(b"junk\xff") is None
    assert parser.feed(b"\xd8first\xff") is None
    assert parser.feed(b"\xd9" + JPEG) == JPEG
    assert parser.buffer == b""
    assert parser.feed(b"\xff\xd8" + b"x" * 1000) is None
    assert len(parser.buffer) <= 64
    assert parser.feed(JPEG) == JPEG
    assert parser.feed(b"\xff\xd8" + b"x" * 100 + b"\xff\xd9" + JPEG) == JPEG
    assert len(parser.buffer) <= 64


class FakePipe:
    def __init__(self, *chunks):
        self.chunks = queue.Queue()
        self.closed = False
        self.reads = 0
        for chunk in chunks:
            self.chunks.put(chunk)

    def read1(self, _size):
        self.reads += 1
        return self.chunks.get(timeout=5)

    def close(self):
        self.closed = True


class FakeProcess:
    def __init__(self, *chunks):
        self.stdout = FakePipe(*chunks)
        self.returncode = None
        self.terminations = 0

    def poll(self):
        return self.returncode

    def terminate(self):
        self.terminations += 1
        self.returncode = 0
        self.stdout.chunks.put(b"")

    def kill(self):
        self.terminate()

    def wait(self, timeout=None):
        return self.returncode


async def test_simultaneous_viewers_share_decoder_and_close_clears_frame(monkeypatch):
    preview = CameraPreview(SecretStr(SOURCE), enabled=True)
    process = FakeProcess(JPEG)
    launch = AsyncMock(return_value=process)
    monkeypatch.setattr(preview, "_launch", launch)
    try:
        frames = await asyncio.gather(preview.get_frame(), preview.get_frame(), preview.get_frame())
        assert all(frame.jpeg == JPEG for frame in frames)
        assert (await preview.get_frame()).jpeg == JPEG
        launch.assert_awaited_once()
    finally:
        await preview.close()
    assert process.terminations == 1
    assert process.stdout.closed
    assert preview._frame is None
    with pytest.raises(CameraUnavailable):
        await preview.get_frame()


async def test_stale_frame_is_never_returned_and_acquisition_is_bounded(monkeypatch):
    preview = CameraPreview(SecretStr(SOURCE), enabled=True,
                            acquisition_seconds=0.03, stale_seconds=0.01)
    preview._frame = PreviewFrame(JPEG, time.monotonic() - 10)
    process = FakeProcess()
    monkeypatch.setattr(preview, "_launch", AsyncMock(return_value=process))
    try:
        with pytest.raises(CameraFrameTimeout):
            await preview.get_frame()
    finally:
        await preview.close()
    assert preview._frame is None


async def test_idle_viewer_cleanup_stops_decoder(monkeypatch):
    preview = CameraPreview(SecretStr(SOURCE), enabled=True, idle_seconds=0.01)
    process = FakeProcess(JPEG)
    monkeypatch.setattr(preview, "_launch", AsyncMock(return_value=process))
    try:
        await preview.get_frame()
        await asyncio.wait_for(asyncio.shield(preview._runner), timeout=1.5)
        assert process.terminations == 1
        assert preview._frame is None
    finally:
        await preview.close()


async def test_stalled_decoder_is_stopped_even_while_viewer_is_active(monkeypatch):
    preview = CameraPreview(SecretStr(SOURCE), enabled=True, stall_seconds=0.03)
    process = FakeProcess()
    monkeypatch.setattr(preview, "_launch", AsyncMock(return_value=process))
    try:
        with pytest.raises(CameraUnavailable):
            await preview.get_frame()
        assert process.terminations == 1
        assert process.stdout.reads == 1
    finally:
        await preview.close()


async def test_process_failure_reconnects_and_never_logs_credential(monkeypatch, caplog):
    preview = CameraPreview(SecretStr(SOURCE), enabled=True)
    process = FakeProcess(JPEG)
    launch = AsyncMock(side_effect=[RuntimeError(SOURCE), process])
    monkeypatch.setattr(preview, "_launch", launch)
    try:
        with pytest.raises(CameraUnavailable):
            await preview.get_frame()
        await asyncio.sleep(1.05)
        assert (await preview.get_frame()).jpeg == JPEG
        assert launch.await_count == 2
        assert SOURCE not in caplog.text
        assert "unit-test-camera-secret" not in caplog.text
    finally:
        await preview.close()


async def test_popen_transport_supports_selector_loops_and_discards_stderr(monkeypatch):
    import imageio_ffmpeg

    monkeypatch.setattr(imageio_ffmpeg, "get_ffmpeg_exe", lambda: "bundled-ffmpeg")
    calls = []
    process = FakeProcess()

    def popen(args, **kwargs):
        calls.append((args, kwargs))
        return process

    monkeypatch.setattr(subprocess, "Popen", popen)
    preview = CameraPreview(SecretStr(SOURCE), enabled=True)
    assert await preview._launch() is process
    args, options = calls[0]
    assert args[args.index("-i") + 1] == SOURCE
    assert options["stderr"] == subprocess.DEVNULL
    assert options["stdout"] == subprocess.PIPE
    assert "fps=15,scale=640:-2" in args


@pytest.mark.parametrize("host,headers,status", [
    ("127.0.0.1", {}, 200),
    ("203.0.113.10", {PROXY_TOKEN_HEADER: TOKEN}, 200),
    ("203.0.113.10", {}, 403),
    ("127.0.0.1", {PROXY_TOKEN_HEADER: "wrong"}, 403),
])
async def test_camera_stream_auth_and_exact_multipart_contract(host, headers, status):
    preview = AsyncMock()
    preview.get_frame.side_effect = [PreviewFrame(JPEG, 1.0), CameraUnavailable()]
    response = await request(make_app(preview), STREAM, host=host, headers=headers)
    assert response.status_code == status
    if status != 200:
        preview.get_frame.assert_not_awaited()
        return
    assert response.headers["content-type"] == (
        "multipart/x-mixed-replace; boundary=classroom-frame"
    )
    assert response.headers["cache-control"] == "no-store, no-transform"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.content == (
        b"--classroom-frame\r\nContent-Type: image/jpeg\r\n"
        + f"Content-Length: {len(JPEG)}\r\n\r\n".encode()
        + JPEG + b"\r\n--classroom-frame--\r\n"
    )
    assert b"rtsp" not in response.content
    assert preview.get_frame.await_args_list[1].kwargs == {"after_received_at": 1.0}


@pytest.mark.parametrize("path,status", [
    (STREAM + "?source=anything", 400),
    (f"/api/v1/classrooms/{uuid4()}/camera/stream", 404),
])
async def test_camera_stream_scope_and_query_parameters_fail_before_decoder(path, status):
    preview = AsyncMock()
    response = await request(make_app(preview), path)
    assert response.status_code == status
    preview.get_frame.assert_not_awaited()


@pytest.mark.parametrize("failure,status", [
    (CameraUnavailable(), 503), (CameraFrameTimeout(), 504),
])
async def test_stream_first_frame_errors_retain_generic_json_status(failure, status):
    preview = AsyncMock()
    preview.get_frame.side_effect = failure
    response = await request(make_app(preview), STREAM)
    assert response.status_code == status
    assert response.headers["content-type"] == "application/json"
    assert response.headers["cache-control"] == "no-store"
    assert "rtsp" not in response.text


async def test_wait_for_new_frame_never_replays_cached_frame(monkeypatch):
    preview = CameraPreview(SecretStr(SOURCE), enabled=True)
    process = FakeProcess(JPEG)
    monkeypatch.setattr(preview, "_launch", AsyncMock(return_value=process))
    try:
        first = await preview.get_frame()
        pending = asyncio.create_task(preview.get_frame(after_received_at=first.received_at))
        await asyncio.sleep(0.01)
        assert not pending.done()
        process.stdout.chunks.put(b"\xff\xd8next-frame\xff\xd9")
        second = await asyncio.wait_for(pending, 1)
        assert second.received_at > first.received_at
        assert second.jpeg != first.jpeg
    finally:
        await preview.close()


async def test_stream_backpressure_reads_only_when_viewer_requests_next_part():
    preview = AsyncMock()
    preview.get_frame.side_effect = [PreviewFrame(b"\xff\xd8new\xff\xd9", 2.0), CameraUnavailable()]
    stream = camera_routes._stream_parts(preview, PreviewFrame(JPEG, 1.0))
    first = await anext(stream)
    assert JPEG in first
    preview.get_frame.assert_not_awaited()
    second = await anext(stream)
    assert b"\xff\xd8new\xff\xd9" in second
    preview.get_frame.assert_awaited_once_with(after_received_at=1.0)
    assert await anext(stream) == b"--classroom-frame--\r\n"
    with pytest.raises(StopAsyncIteration):
        await anext(stream)


async def test_stream_session_deadline_cancels_wait_for_a_new_frame(monkeypatch):
    monkeypatch.setattr(camera_routes, "STREAM_SECONDS", 0.02)
    wait_cancelled = asyncio.Event()

    async def stalled_frame(**_kwargs):
        try:
            await asyncio.Event().wait()
        finally:
            wait_cancelled.set()

    preview = AsyncMock()
    preview.get_frame.side_effect = stalled_frame
    parts = [part async for part in camera_routes._stream_parts(preview, PreviewFrame(JPEG, 1.0))]
    assert len(parts) == 2
    assert parts[-1] == b"--classroom-frame--\r\n"
    assert wait_cancelled.is_set()


async def test_client_disconnect_cancels_stream_wait_without_closing_shared_decoder():
    first_sent = asyncio.Event()
    wait_cancelled = asyncio.Event()

    async def next_frame(**_kwargs):
        try:
            await asyncio.Event().wait()
        finally:
            wait_cancelled.set()

    preview = AsyncMock()
    preview.get_frame.side_effect = next_frame
    response = camera_routes.StreamingResponse(
        camera_routes._stream_parts(preview, PreviewFrame(JPEG, 1.0))
    )

    async def receive():
        await first_sent.wait()
        # Let the body task start waiting for its next frame before disconnect.
        await asyncio.sleep(0)
        return {"type": "http.disconnect"}

    async def send(message):
        if message["type"] == "http.response.body":
            first_sent.set()

    await response({"type": "http", "asgi": {"spec_version": "2.0"}}, receive, send)
    assert wait_cancelled.is_set()
    preview.close.assert_not_awaited()
