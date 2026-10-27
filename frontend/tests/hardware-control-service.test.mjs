import assert from "node:assert/strict";
import { afterEach, test } from "node:test";
import { HardwareControlService, getHardwareControlMode, isHardwareDeviceStateFresh, isLocalHardwareControlEnabled, withHardwareState } from "../src/api/hardware-control-service.ts";

const ORIGIN = "https://nexergy.justin58094.workers.dev";
const CLASSROOM = "26e97d46-7a34-4439-922a-83684bda557d";
const DEVICE = "d7958a13-4c75-47e8-8b5e-4f4e00000012";
const COMMAND = "00000000-0000-4000-8000-000000000001";
const ENV_KEYS = ["NEXT_PUBLIC_ENABLE_HARDWARE_CONTROLS", "NEXT_PUBLIC_ENABLE_REMOTE_HARDWARE_CONTROLS", "NEXT_PUBLIC_HARDWARE_CONTROL_ORIGIN", "NEXT_PUBLIC_API_BASE_URL"];
const initialEnv = Object.fromEntries(ENV_KEYS.map((key) => [key, process.env[key]]));
const originalWindow = globalThis.window;
const originalFetch = globalThis.fetch;
afterEach(() => {
  for (const key of ENV_KEYS) {
    if (initialEnv[key] === undefined) delete process.env[key];
    else process.env[key] = initialEnv[key];
  }
  if (originalWindow === undefined) delete globalThis.window;
  else globalThis.window = originalWindow;
  globalThis.fetch = originalFetch;
});

function configure(url = ORIGIN) {
  globalThis.window = { location: new URL(url) };
  process.env.NEXT_PUBLIC_ENABLE_HARDWARE_CONTROLS = "false";
  process.env.NEXT_PUBLIC_ENABLE_REMOTE_HARDWARE_CONTROLS = "true";
  process.env.NEXT_PUBLIC_HARDWARE_CONTROL_ORIGIN = ORIGIN;
  process.env.NEXT_PUBLIC_API_BASE_URL = "";
}
function json(body, status = 200) { return Response.json(body, { status }); }
function request(patch = { is_on: true }) { return { device_id: DEVICE, patch, reason: "User requested relay change" }; }
function allowed() { return json({ decision: "allowed", command_id: COMMAND, reason_codes: [] }); }
function applied(status = "applied", overrides = {}) {
  return json({ command_id: COMMAND, device_id: DEVICE, status, reason_code: null, ...overrides });
}

test("server rendering never enables hardware controls", () => {
  delete globalThis.window;
  process.env.NEXT_PUBLIC_ENABLE_HARDWARE_CONTROLS = "true";
  process.env.NEXT_PUBLIC_ENABLE_REMOTE_HARDWARE_CONTROLS = "true";
  assert.equal(getHardwareControlMode(), "demo");
});
for (const host of ["localhost", "127.0.0.1", "[::1]"]) {
  test(`local controls retain explicit opt-in on ${host}`, () => {
    configure(`http://${host}:3000`);
    assert.equal(getHardwareControlMode(), "demo");
    process.env.NEXT_PUBLIC_ENABLE_HARDWARE_CONTROLS = "true";
    assert.equal(getHardwareControlMode(), "local");
    assert.equal(isLocalHardwareControlEnabled(), true);
  });
}
test("remote controls require their separate flag and exact HTTPS origin", () => {
  configure();
  assert.equal(getHardwareControlMode(), "remote");
  assert.equal(isLocalHardwareControlEnabled(), false);
  process.env.NEXT_PUBLIC_ENABLE_REMOTE_HARDWARE_CONTROLS = "false";
  assert.equal(getHardwareControlMode(), "demo");
});
for (const url of ["http://nexergy.justin58094.workers.dev", "https://other.example", "https://nexergy.justin58094.workers.dev.evil.example"]) {
  test(`remote controls reject unexpected origin ${url}`, () => {
    configure(url);
    assert.equal(getHardwareControlMode(), "demo");
  });
}
for (const base of ["http://localhost:8000", "https://api.example", "/", " "]) {
  test(`remote mode rejects nonempty API base ${base}`, () => {
    configure();
    process.env.NEXT_PUBLIC_API_BASE_URL = base;
    assert.equal(getHardwareControlMode(), "demo");
  });
}
test("remote service fetches state/status through same-origin authenticated requests", async () => {
  configure();
  const calls = [];
  globalThis.fetch = async (url, init) => {
    calls.push({ url, init });
    return json(url.endsWith("/devices") ? { classroom_id: CLASSROOM, devices: [] } : { status: "inactive" });
  };
  const service = new HardwareControlService(CLASSROOM);
  await service.getDeviceStates();
  await service.getUrgencyStatus();
  assert.deepEqual(calls.map((call) => call.url), [`/api/v1/classrooms/${CLASSROOM}/devices`, `/api/v1/classrooms/${CLASSROOM}/urgency`]);
  for (const { init } of calls) {
    assert.equal(init.credentials, "include");
    assert.equal(init.cache, "no-store");
    assert.ok(init.signal instanceof AbortSignal);
  }
});
test("remote urgency activation is blocked before any request", async () => {
  configure();
  let calls = 0;
  globalThis.fetch = async () => { calls++; return json({}); };
  await assert.rejects(new HardwareControlService(CLASSROOM).activateUrgency(), /localhost only/);
  assert.equal(calls, 0);
});
test("local urgency activation remains available", async () => {
  configure("http://localhost:3000");
  process.env.NEXT_PUBLIC_ENABLE_HARDWARE_CONTROLS = "true";
  process.env.NEXT_PUBLIC_API_BASE_URL = "http://localhost:8000";
  globalThis.fetch = async (url, init) => {
    assert.equal(url, `http://localhost:8000/api/v1/classrooms/${CLASSROOM}/urgency`);
    assert.equal(init.method, "POST");
    return json({ status: "monitoring" });
  };
  assert.equal((await new HardwareControlService(CLASSROOM).activateUrgency()).status, "monitoring");
});
for (const patch of [{ is_on: true }, { is_on: false }, { mode: "auto" }, { mode: "manual" }]) {
  test(`relay patch ${JSON.stringify(patch)} waits for matching applied acknowledgement`, async () => {
    configure();
    const calls = [];
    globalThis.fetch = async (url, init) => {
      calls.push({ url, init });
      return calls.length === 1 ? allowed() : applied();
    };
    await new HardwareControlService(CLASSROOM).requestControl(request(patch));
    assert.equal(calls.length, 2);
    assert.equal(calls[0].url, `/api/v1/devices/${DEVICE}/commands`);
    assert.equal(calls[1].url, `/api/v1/commands/${COMMAND}`);
    assert.match(calls[0].init.headers["Idempotency-Key"], /^[0-9a-f-]{36}$/);
    const body = JSON.parse(calls[0].init.body);
    assert.equal(body.expires_in_seconds, 30);
    assert.deepEqual(body.value, patch.mode ? { mode: patch.mode } : { on: patch.is_on });
  });
}
for (const patch of [{ fan_speed: 2 }, { brightness_pct: 50 }, { target_temperature_c: 26 }, { mode: "manual", is_on: true }, {}, { is_on: "true" }]) {
  test(`unsupported patch ${JSON.stringify(patch)} never sends a command`, async () => {
    configure();
    let calls = 0;
    globalThis.fetch = async () => { calls++; return json({}); };
    await assert.rejects(new HardwareControlService(CLASSROOM).requestControl(request(patch)));
    assert.equal(calls, 0);
  });
}
test("policy denial stops before acknowledgement polling", async () => {
  configure();
  let calls = 0;
  globalThis.fetch = async () => { calls++; return json({ decision: "denied", command_id: null, reason_codes: ["device_state_stale"] }); };
  await assert.rejects(new HardwareControlService(CLASSROOM).requestControl(request()), /stale/);
  assert.equal(calls, 1);
});
for (const status of ["rejected", "failed", "timed_out"]) {
  test(`terminal acknowledgement ${status} is not reported as applied`, async () => {
    configure();
    let calls = 0;
    globalThis.fetch = async () => ++calls === 1 ? allowed() : applied(status);
    await assert.rejects(new HardwareControlService(CLASSROOM).requestControl(request()), /did not apply|timed out|expired/);
  });
}
test("a mismatched acknowledgement cannot confirm another device request", async () => {
  configure();
  let calls = 0;
  globalThis.fetch = async () => ++calls === 1 ? allowed() : applied("applied", { device_id: "another-device" });
  await assert.rejects(new HardwareControlService(CLASSROOM).requestControl(request()), /did not match/);
});
for (const status of [401, 403]) {
  test(`authorization failure ${status} rejects without mock fallback`, async () => {
    configure();
    let calls = 0;
    globalThis.fetch = async () => { calls++; return json({}, status); };
    await assert.rejects(new HardwareControlService(CLASSROOM).getDeviceStates(), /Sign in|not authorized/);
    assert.equal(calls, 1);
  });
}

for (const [detail, message] of [
  ["Your Cloudflare account is not authorized for relay controls.", /allowed operator account/],
  ["Private monitoring bridge access denied.", /backend remote control and operator settings/],
  ["This website origin is not allowed.", /configured website origin/],
  ["This request origin is not allowed.", /Reload the configured website/],
  ["Device control is localhost-only.", /authenticated remote control configuration/],
]) {
  test(`403 detail ${detail} explains the actual access failure`, async () => {
    configure();
    globalThis.fetch = async () => json({ detail }, 403);
    await assert.rejects(new HardwareControlService(CLASSROOM).getDeviceStates(), message);
  });
}

test("an unknown 403 detail uses safe configuration guidance", async () => {
  configure();
  globalThis.fetch = async () => json({ detail: "unexpected private upstream contents" }, 403);
  await assert.rejects(new HardwareControlService(CLASSROOM).getDeviceStates(), (error) => {
    assert.match(error.message, /account and remote control configuration/);
    assert.doesNotMatch(error.message, /private upstream contents/);
    return true;
  });
});
test("network failures reject rather than inventing a successful state", async () => {
  configure();
  globalThis.fetch = async () => { throw new Error("Network unavailable"); };
  await assert.rejects(new HardwareControlService(CLASSROOM).getDeviceStates(), /Network unavailable/);
});
test("a service created in demo mode cannot send hardware requests", async () => {
  configure("https://unconfigured.example");
  let calls = 0;
  globalThis.fetch = async () => { calls++; return json({}); };
  const service = new HardwareControlService(CLASSROOM);
  await assert.rejects(service.getDeviceStates(), /not enabled/);
  await assert.rejects(service.requestControl(request()), /not enabled/);
  assert.equal(calls, 0);
});

test("a cached server fresh flag cannot keep old device state interactive", () => {
  const now = Date.parse("2026-10-26T12:00:00Z");
  const state = {
    operational_status: "online", freshness: "fresh", fault_codes: [],
    reported_state: { on: true, mode: "manual" }, reported_at: "2026-10-26T11:59:59Z",
  };
  assert.equal(isHardwareDeviceStateFresh(state, now), true);
  assert.equal(isHardwareDeviceStateFresh({ ...state, reported_at: "2026-10-26T11:00:00Z" }, now), false);
  assert.equal(isHardwareDeviceStateFresh({ ...state, reported_at: "2026-10-26T12:00:06Z" }, now), false);
  assert.equal(isHardwareDeviceStateFresh({ ...state, reported_at: null }, now), false);
  assert.equal(isHardwareDeviceStateFresh({ ...state, reported_state: {} }, now), false);
  assert.equal(isHardwareDeviceStateFresh({ ...state, reported_state: { on: true, mode: "unknown" } }, now), false);
  assert.equal(isHardwareDeviceStateFresh({ ...state, fault_codes: ["relay_fault"] }, now), false);
  assert.equal(isHardwareDeviceStateFresh({ ...state, freshness: "stale" }, now), false);
  assert.equal(isHardwareDeviceStateFresh(undefined, now), false);
});

const PRESENTATION_NOW = Date.parse("2026-10-26T12:00:00Z");
const dashboardDevice = {
  id: DEVICE, name: "Classroom fan", location: "Classroom", kind: "fan",
  is_on: false, mode: "auto", connected: true, pending: false,
  target_temperature_c: null, fan_speed: 2, brightness_pct: null,
  active_power_w: 50, status_label: "Demo state", updated_at: "2026-10-26T12:00:00Z",
};
function reportedDevice(overrides = {}) {
  return {
    device_id: DEVICE, type: "fan", operational_status: "online", freshness: "fresh",
    reported_state: { on: true, mode: "manual" }, reported_at: "2026-10-26T11:59:59Z",
    fault_codes: [], pending_command_id: null, ...overrides,
  };
}
function presentation(devices, error = null, pending = new Set()) {
  return withHardwareState(dashboardDevice, devices, error, pending, PRESENTATION_NOW);
}

test("hardware access failure locks even fresh controls without claiming controller disconnection", () => {
  const device = presentation([reportedDevice()], "This request is not authorized.");
  assert.equal(device.connected, false);
  assert.equal(device.status_label, "Controls unavailable");
  assert.match(device.control_lock_reason, /hardware access is restored/);
  assert.doesNotMatch(device.control_lock_reason, /reconnect/);
});

test("missing controller state stays locked while its connection remains unknown", () => {
  const device = presentation(undefined);
  assert.equal(device.connected, false);
  assert.equal(device.status_label, "Controller state unavailable");
  assert.doesNotMatch(device.control_lock_reason, /reconnect/);
});

test("an explicitly offline controller retains reconnect guidance", () => {
  const device = presentation([reportedDevice({ operational_status: "offline" })]);
  assert.equal(device.connected, false);
  assert.equal(device.status_label, "Controller offline");
  assert.match(device.control_lock_reason, /controller reconnects/);
});

test("stale state and faults remain locked with their respective explanations", () => {
  const stale = presentation([reportedDevice({ reported_at: "2026-10-26T11:00:00Z" })]);
  assert.equal(stale.connected, false);
  assert.match(stale.status_label, /stale/);
  assert.match(stale.control_lock_reason, /fresh controller state/);
  const fault = presentation([reportedDevice({ fault_codes: ["relay_fault"] })]);
  assert.equal(fault.connected, false);
  assert.equal(fault.status_label, "Device fault");
  assert.match(fault.control_lock_reason, /reports a fault/);
});

test("fresh confirmed state remains usable and pending acknowledgement stays pending", () => {
  const device = presentation([reportedDevice()]);
  assert.equal(device.connected, true);
  assert.equal(device.is_on, true);
  assert.equal(device.mode, "manual");
  assert.equal(device.control_lock_reason, undefined);
  const pending = presentation([reportedDevice()], null, new Set([DEVICE]));
  assert.equal(pending.connected, true);
  assert.equal(pending.pending, true);
  assert.equal(pending.status_label, "Awaiting ESP32 acknowledgement");
});
