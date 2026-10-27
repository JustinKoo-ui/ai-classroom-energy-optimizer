import type { UUID } from "@/src/types/contracts";

export const MAX_CAMERA_FRAME_BYTES = 2 * 1024 * 1024;
const MAX_HEADER_BYTES = 2048;
const BOUNDARY = new TextEncoder().encode("--classroom-frame");
const HEADER_END = new Uint8Array([13, 10, 13, 10]);

function invalidFrame(): Error {
  return new Error("The camera returned an invalid preview stream.");
}

function indexOf(bytes: Uint8Array, pattern: Uint8Array): number {
  outer: for (let offset = 0; offset <= bytes.length - pattern.length; offset += 1) {
    for (let index = 0; index < pattern.length; index += 1) {
      if (bytes[offset + index] !== pattern[index]) continue outer;
    }
    return offset;
  }
  return -1;
}

/** Bounded multipart decoder. Only the newest complete JPEG from a chunk survives. */
export class CameraFrameParser {
  private buffer: Uint8Array = new Uint8Array();
  private frameLength: number | null = null;
  private ended = false;

  push(chunk: Uint8Array): Uint8Array | null {
    let latest: Uint8Array | null = null;
    // Split large transport chunks before buffering, including chunks containing
    // many frames, so stream duration never determines memory consumption.
    for (let offset = 0; offset < chunk.length; offset += 64 * 1024) {
      if (this.ended) throw invalidFrame();
      const slice = chunk.subarray(offset, offset + 64 * 1024);
      const joined = new Uint8Array(this.buffer.length + slice.length);
      joined.set(this.buffer);
      joined.set(slice, this.buffer.length);
      this.buffer = joined;
      const frame = this.consume();
      if (frame) latest = frame;
    }
    return latest;
  }

  private consume(): Uint8Array | null {
    let latest: Uint8Array | null = null;
    for (;;) {
      if (this.frameLength === null) {
        for (let index = 0; index < Math.min(this.buffer.length, BOUNDARY.length); index += 1) {
          if (this.buffer[index] !== BOUNDARY[index]) throw invalidFrame();
        }
        if (this.buffer.length < BOUNDARY.length + 4) return latest;
        if (this.buffer[BOUNDARY.length] === 45 && this.buffer[BOUNDARY.length + 1] === 45) {
          if (this.buffer[BOUNDARY.length + 2] !== 13 || this.buffer[BOUNDARY.length + 3] !== 10
            || this.buffer.length !== BOUNDARY.length + 4) throw invalidFrame();
          this.buffer = new Uint8Array();
          this.ended = true;
          return latest;
        }
        const end = indexOf(this.buffer, HEADER_END);
        if (end < 0) {
          if (this.buffer.length > MAX_HEADER_BYTES) throw invalidFrame();
          return latest;
        }
        if (end + HEADER_END.length > MAX_HEADER_BYTES) throw invalidFrame();
        const header = new TextDecoder().decode(this.buffer.subarray(0, end + HEADER_END.length));
        const match = header.match(/^--classroom-frame\r\nContent-Type: image\/jpeg\r\nContent-Length: ([1-9]\d*)\r\n\r\n$/i);
        const length = match ? Number(match[1]) : 0;
        if (!Number.isSafeInteger(length) || length < 4 || length > MAX_CAMERA_FRAME_BYTES) throw invalidFrame();
        this.frameLength = length;
        this.buffer = this.buffer.subarray(end + HEADER_END.length);
      }
      const length = this.frameLength;
      if (this.buffer.length < length + 2) return latest;
      if (this.buffer[0] !== 0xff || this.buffer[1] !== 0xd8
        || this.buffer[length - 2] !== 0xff || this.buffer[length - 1] !== 0xd9
        || this.buffer[length] !== 13 || this.buffer[length + 1] !== 10) throw invalidFrame();
      latest = this.buffer.slice(0, length);
      this.buffer = this.buffer.subarray(length + 2);
      this.frameLength = null;
    }
  }

  finish(): void {
    if (!this.ended || this.buffer.length || this.frameLength !== null) throw invalidFrame();
  }
}

/** A single authenticated HTTP connection carries successive memory-only JPEGs. */
export async function* streamCameraPreviewFrames(
  apiBaseUrl: string,
  classroomId: UUID,
  signal: AbortSignal,
): AsyncGenerator<Blob> {
  const response = await fetch(
    `${apiBaseUrl}/api/v1/classrooms/${classroomId}/camera/stream`,
    { credentials: "include", cache: "no-store", signal, headers: { Accept: "multipart/x-mixed-replace" } },
  );
  if (!response.ok) {
    if (response.status === 401 || response.status === 403) {
      throw new Error("Camera access requires a signed-in monitoring session.");
    }
    if (response.status === 503) {
      throw new Error("Camera preview is unavailable. Check the camera connection and backend preview setting.");
    }
    throw new Error("The camera could not be reached. Retrying automatically.");
  }
  if (!/^multipart\/x-mixed-replace\s*;\s*boundary=(?:classroom-frame|"classroom-frame")\s*$/i.test(
    response.headers.get("Content-Type") ?? "",
  ) || !response.body) {
    await response.body?.cancel();
    throw new Error("No camera stream was received. Check your monitoring session.");
  }
  const reader = response.body.getReader();
  const parser = new CameraFrameParser();
  const onAbort = () => { void reader.cancel().catch(() => undefined); };
  signal.addEventListener("abort", onAbort, { once: true });
  try {
    for (;;) {
      signal.throwIfAborted();
      const { value, done } = await reader.read();
      signal.throwIfAborted();
      if (done) {
        parser.finish();
        return;
      }
      const frame = parser.push(value);
      if (frame) yield new Blob([new Uint8Array(frame).buffer], { type: "image/jpeg" });
    }
  } finally {
    signal.removeEventListener("abort", onAbort);
    await reader.cancel().catch(() => undefined);
    reader.releaseLock();
  }
}
