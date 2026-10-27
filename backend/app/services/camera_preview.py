"""On-demand, memory-only RTSP preview shared by all authorized viewers."""

from __future__ import annotations

import asyncio
import os
import subprocess
import time
from contextlib import suppress
from dataclasses import dataclass
from urllib.parse import urlsplit

from pydantic import SecretStr

MAX_JPEG_BYTES = 2 * 1024 * 1024


class CameraUnavailable(Exception):
    """The configured decoder or camera is temporarily unavailable."""


class CameraFrameTimeout(Exception):
    """No fresh frame arrived within the acquisition budget."""


@dataclass(frozen=True, repr=False)
class PreviewFrame:
    jpeg: bytes
    received_at: float


class JpegParser:
    """Keep only the latest complete image and bound incomplete frame storage."""

    def __init__(self, max_bytes: int = MAX_JPEG_BYTES) -> None:
        self.max_bytes = max_bytes
        self.buffer = bytearray()

    def feed(self, chunk: bytes) -> bytes | None:
        self.buffer.extend(chunk)
        latest = None
        while True:
            start = self.buffer.find(b"\xff\xd8")
            if start < 0:
                self.buffer[:] = self.buffer[-1:] if self.buffer.endswith(b"\xff") else b""
                break
            if start:
                del self.buffer[:start]
            end = self.buffer.find(b"\xff\xd9", 2)
            if end < 0:
                if len(self.buffer) > self.max_bytes:
                    self.buffer[:] = self.buffer[-1:] if self.buffer.endswith(b"\xff") else b""
                break
            end += 2
            if end <= self.max_bytes:
                latest = bytes(self.buffer[:end])
            del self.buffer[:end]
        return latest


class CameraPreview:
    def __init__(
        self,
        source: SecretStr | None,
        *,
        enabled: bool = False,
        idle_seconds: float = 15,
        stale_seconds: float = 2,
        acquisition_seconds: float = 8,
        stall_seconds: float = 5,
    ) -> None:
        self._source = source
        self.enabled = enabled
        self._idle_seconds = idle_seconds
        self._stale_seconds = stale_seconds
        self._acquisition_seconds = acquisition_seconds
        self._stall_seconds = stall_seconds
        self._frame: PreviewFrame | None = None
        self._runner: asyncio.Task[None] | None = None
        self._process: subprocess.Popen[bytes] | None = None
        self._last_request = 0.0
        self._changed = asyncio.Event()
        self._failed = False
        self._closed = False

    def _configured(self) -> bool:
        if not self.enabled or self._source is None or self._closed:
            return False
        try:
            parsed = urlsplit(self._source.get_secret_value())
            return parsed.scheme in {"rtsp", "rtsps"} and bool(parsed.hostname)
        except ValueError:
            return False

    def _active(self) -> bool:
        return not self._closed and time.monotonic() - self._last_request < self._idle_seconds

    async def get_frame(self, *, after_received_at: float | None = None) -> PreviewFrame:
        if not self._configured():
            raise CameraUnavailable()
        self._last_request = time.monotonic()
        # No await occurs before assigning the task: simultaneous viewers share
        # exactly one decoder on this application's event loop.
        if self._runner is None or self._runner.done():
            self._failed = False
            self._runner = asyncio.create_task(self._run())
        deadline = time.monotonic() + self._acquisition_seconds
        while True:
            self._changed.clear()
            frame = self._frame
            if (
                frame is not None
                and time.monotonic() - frame.received_at < self._stale_seconds
                and (after_received_at is None or frame.received_at > after_received_at)
            ):
                return frame
            if self._failed or self._closed:
                raise CameraUnavailable()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise CameraFrameTimeout()
            try:
                await asyncio.wait_for(self._changed.wait(), timeout=remaining)
            except TimeoutError:
                raise CameraFrameTimeout() from None

    async def _launch(self) -> subprocess.Popen[bytes]:
        import imageio_ffmpeg

        assert self._source is not None
        # Never inherit stderr: camera implementations and FFmpeg can include
        # the credential-bearing input URL in diagnostics.
        arguments = [
            imageio_ffmpeg.get_ffmpeg_exe(),
            "-nostdin", "-hide_banner", "-loglevel", "quiet",
            "-rtsp_transport", "tcp", "-timeout", "5000000",
            "-threads", "2", "-i", self._source.get_secret_value(),
            "-an", "-sn", "-dn", "-vf", "fps=15,scale=640:-2",
            "-threads", "1", "-q:v", "6", "-f", "image2pipe",
            "-vcodec", "mjpeg", "pipe:1",
        ]
        # Uvicorn --reload uses Windows' Selector loop, which cannot launch
        # asyncio subprocesses. Popen and threaded pipe reads support it too.
        return subprocess.Popen(
            arguments,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            creationflags=0x08000000 if os.name == "nt" else 0,
        )

    async def _read(self, process: subprocess.Popen[bytes]) -> None:
        assert process.stdout is not None
        parser = JpegParser()
        last_frame_at = time.monotonic()
        pending: asyncio.Task[bytes] | None = None
        try:
            while self._active():
                if time.monotonic() - last_frame_at >= self._stall_seconds:
                    raise CameraUnavailable()
                if pending is None:
                    pending = asyncio.create_task(
                        asyncio.to_thread(process.stdout.read1, 64 * 1024)
                    )
                try:
                    # Retain the same pipe-read task on timeout. In particular,
                    # a stalled camera must not accumulate blocked threads.
                    chunk = await asyncio.wait_for(
                        asyncio.shield(pending), timeout=min(1, self._stall_seconds)
                    )
                except TimeoutError:
                    continue
                pending = None
                if not chunk:
                    raise CameraUnavailable()
                jpeg = parser.feed(chunk)
                if jpeg is not None:
                    last_frame_at = time.monotonic()
                    self._frame = PreviewFrame(jpeg=jpeg, received_at=last_frame_at)
                    self._failed = False
                    self._changed.set()
        finally:
            if pending is not None:
                pending.cancel()
                with suppress(asyncio.CancelledError):
                    await pending

    @staticmethod
    async def _stop_process(process: subprocess.Popen[bytes]) -> None:
        if process.poll() is None:
            with suppress(ProcessLookupError):
                process.terminate()
            try:
                await asyncio.to_thread(process.wait, timeout=2)
            except subprocess.TimeoutExpired:
                with suppress(ProcessLookupError):
                    process.kill()
                await asyncio.to_thread(process.wait)
        if process.stdout is not None:
            process.stdout.close()

    async def _run(self) -> None:
        try:
            while self._active():
                try:
                    self._process = await self._launch()
                    await self._read(self._process)
                except Exception:
                    # Failures are deliberately generic and never logged with
                    # exception text, process arguments, URL or raw image data.
                    self._frame = None
                    self._failed = True
                    self._changed.set()
                finally:
                    if self._process is not None:
                        await self._stop_process(self._process)
                        self._process = None
                if self._active():
                    await asyncio.sleep(1)
                    self._failed = False
        finally:
            self._frame = None
            self._changed.set()

    async def close(self) -> None:
        self._closed = True
        self._changed.set()
        if self._runner is not None:
            self._runner.cancel()
            with suppress(asyncio.CancelledError):
                await self._runner
        # A close can interrupt the runner while it is already awaiting process
        # termination. Complete that cleanup before releasing the service.
        if self._process is not None:
            await self._stop_process(self._process)
            self._process = None
        self._frame = None
