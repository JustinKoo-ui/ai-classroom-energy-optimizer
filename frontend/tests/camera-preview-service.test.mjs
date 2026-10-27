import assert from "node:assert/strict";
import test from "node:test";
import {
  CameraFrameParser, MAX_CAMERA_FRAME_BYTES, streamCameraPreviewFrames,
} from "../src/api/camera-preview-service.ts";

const encoder = new TextEncoder();
const CLASSROOM = "26e97d46-7a34-4439-922a-83684bda557d";
const MIME = "multipart/x-mixed-replace; boundary=classroom-frame";
const END = encoder.encode("--classroom-frame--\r\n");
const jpeg = (value = 1) => new Uint8Array([0xff, 0xd8, value, 0xff, 0xd9]);
function join(...chunks) {
  const result = new Uint8Array(chunks.reduce((size, chunk) => size + chunk.length, 0));
  let offset = 0;
  for (const chunk of chunks) { result.set(chunk, offset); offset += chunk.length; }
  return result;
}
function part(frame = jpeg(), header) {
  return join(encoder.encode(header ?? `--classroom-frame\r\nContent-Type: image/jpeg\r\nContent-Length: ${frame.length}\r\n\r\n`), frame, encoder.encode("\r\n"));
}

test("multipart JPEG survives every possible transport split", () => {
  const bytes = join(part(), END);
  for (let split = 0; split <= bytes.length; split += 1) {
    const parser = new CameraFrameParser();
    const frames = [parser.push(bytes.subarray(0, split)), parser.push(bytes.subarray(split))].filter(Boolean);
    assert.deepEqual(frames, [jpeg()]);
    parser.finish();
  }
});

test("one-byte transport chunks and JPEG bytes resembling a boundary are safe", () => {
  const frame = join(new Uint8Array([0xff, 0xd8]), encoder.encode("--classroom-frame\r\nContent-Length: 20\r\n"), new Uint8Array([0xff, 0xd9]));
  const parser = new CameraFrameParser();
  const frames = [];
  for (const byte of join(part(frame), END)) {
    const result = parser.push(new Uint8Array([byte]));
    if (result) frames.push(result);
  }
  parser.finish();
  assert.deepEqual(frames, [frame]);
});

test("a network burst keeps only its newest complete frame", () => {
  const parser = new CameraFrameParser();
  assert.deepEqual(parser.push(join(part(jpeg(1)), part(jpeg(2)), part(jpeg(3)), END)), jpeg(3));
  parser.finish();
});

test("large transport chunks with many small frames remain bounded and preserve newest frame", () => {
  const parser = new CameraFrameParser();
  const chunks = Array.from({ length: 2000 }, (_, index) => part(jpeg(index % 256)));
  assert.deepEqual(parser.push(join(...chunks, END)), jpeg(1999 % 256));
  parser.finish();
});

for (const [name, bytes] of [
  ["foreign boundary", encoder.encode("--foreign-boundary\r\n\r\n")],
  ["missing content length", part(jpeg(), "--classroom-frame\r\nContent-Type: image/jpeg\r\n\r\n")],
  ["negative length", part(jpeg(), "--classroom-frame\r\nContent-Type: image/jpeg\r\nContent-Length: -1\r\n\r\n")],
  ["oversized length", part(jpeg(), `--classroom-frame\r\nContent-Type: image/jpeg\r\nContent-Length: ${MAX_CAMERA_FRAME_BYTES + 1}\r\n\r\n`)],
  ["overlong header", encoder.encode("--classroom-frame\r\n" + "x".repeat(3000))],
  ["wrong MIME", part(jpeg(), "--classroom-frame\r\nContent-Type: text/html\r\nContent-Length: 5\r\n\r\n")],
  ["non JPEG payload", part(new Uint8Array([1, 2, 3, 4, 5]))],
  ["missing JPEG end marker", part(new Uint8Array([0xff, 0xd8, 1, 2, 3]))],
  ["data after final delimiter", join(END, encoder.encode("extra"))],
]) {
  test(`malformed camera stream rejects ${name}`, () => {
    assert.throws(() => new CameraFrameParser().push(bytes), /invalid preview stream/);
  });
}

test("truncated HTTP body is a failure, not a normal stream renewal", () => {
  const parser = new CameraFrameParser();
  parser.push(part());
  assert.throws(() => parser.finish(), /invalid preview stream/);
});

test("stream fetch is authenticated, no-store and cancels its reader when caller stops", async (context) => {
  let request;
  let cancelled = false;
  context.mock.method(globalThis, "fetch", async (url, init) => {
    request = { url, init };
    return new Response(new ReadableStream({
      start(controller) { controller.enqueue(part()); },
      cancel() { cancelled = true; },
    }), { headers: { "Content-Type": MIME } });
  });
  const signal = new AbortController().signal;
  for await (const frame of streamCameraPreviewFrames("https://monitor.example", CLASSROOM, signal)) {
    assert.equal(frame.type, "image/jpeg");
    assert.deepEqual(new Uint8Array(await frame.arrayBuffer()), jpeg());
    break;
  }
  assert.equal(request.url, `https://monitor.example/api/v1/classrooms/${CLASSROOM}/camera/stream`);
  assert.equal(request.init.credentials, "include");
  assert.equal(request.init.cache, "no-store");
  assert.equal(request.init.signal, signal);
  assert.equal(cancelled, true);
});

test("abort cancels a stalled response read promptly", async (context) => {
  let cancelled = false;
  context.mock.method(globalThis, "fetch", async () => new Response(new ReadableStream({
    cancel() { cancelled = true; },
  }), { headers: { "Content-Type": MIME } }));
  const controller = new AbortController();
  const iterator = streamCameraPreviewFrames("", CLASSROOM, controller.signal);
  const pending = iterator.next();
  await new Promise((resolve) => setImmediate(resolve));
  controller.abort();
  await assert.rejects(pending, { name: "AbortError" });
  assert.equal(cancelled, true);
});

test("normal completed stream returns without an error for immediate renewal", async (context) => {
  context.mock.method(globalThis, "fetch", async () => new Response(join(part(), END), { headers: { "Content-Type": MIME } }));
  const frames = [];
  for await (const frame of streamCameraPreviewFrames("", CLASSROOM, new AbortController().signal)) frames.push(frame);
  assert.equal(frames.length, 1);
});

for (const [name, response, message] of [
  ["Access login HTML", () => new Response("<html>login</html>", { headers: { "Content-Type": "text/html" } }), /monitoring session/],
  ["wrong boundary", () => new Response(END, { headers: { "Content-Type": "multipart/x-mixed-replace; boundary=other" } }), /monitoring session/],
  ["unauthorized", () => new Response("private-secret-detail", { status: 403 }), /signed-in monitoring/],
  ["unavailable", () => new Response("rtsp://private-secret", { status: 503 }), /preview is unavailable/],
]) {
  test(`stream ${name} gives a generic error`, async (context) => {
    context.mock.method(globalThis, "fetch", async () => response());
    await assert.rejects(streamCameraPreviewFrames("", CLASSROOM, new AbortController().signal).next(), message);
  });
}
