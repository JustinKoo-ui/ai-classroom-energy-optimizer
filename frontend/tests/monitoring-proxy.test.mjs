import assert from "node:assert/strict";
import test from "node:test";
import { exportJWK, generateKeyPair, SignJWT } from "jose";
import { createMonitoringProxy } from "../worker/monitoring-proxy.ts";

const ORIGIN = "https://nexergy.justin58094.workers.dev";
const ISSUER = "https://classroom-test.cloudflareaccess.com";
const AUDIENCE = "classroom-test-audience-0123456789";
const CLASSROOM = "26e97d46-7a34-4439-922a-83684bda557d";
const BASE = `/api/v1/classrooms/${CLASSROOM}`;
const CAMERA_PATH = `${BASE}/camera/frame`;
const STREAM_PATH = `${BASE}/camera/stream`;
const BRIDGE_SECRET = "unit-test-private-bridge-secret-with-more-than-32-characters";
const REPORT_TOKEN = "unit-test-separate-report-token";
const OPERATOR = "operator@example.com";
const CONTROL_ENV = {
  CLASSROOM_REMOTE_CONTROL_ENABLED: "true",
  CLASSROOM_REMOTE_CONTROL_ALLOWED_EMAILS: OPERATOR,
};
const pair = await generateKeyPair("RS256", { extractable: true });
const otherPair = await generateKeyPair("RS256", { extractable: true });
const jwk = { ...await exportJWK(pair.publicKey), kid: "test-access-key", alg: "RS256", use: "sig" };

async function assertion(overrides = {}, { key = pair.privateKey, alg = "RS256", omit = [] } = {}) {
  const now = Math.floor(Date.now() / 1000);
  const claims = {
    iss: ISSUER,
    aud: AUDIENCE,
    sub: "unit-test-person",
    iat: now,
    exp: now + 300,
    ...overrides,
  };
  for (const claim of omit) delete claims[claim];
  return new SignJWT(claims).setProtectedHeader({ alg, kid: "test-access-key" }).sign(key);
}

function harness({ env: overrides = {}, upstream, jwks } = {}) {
  const upstreamRequests = [];
  const jwksRequests = [];
  const env = {
    CLASSROOM_API_CLASSROOM_ID: CLASSROOM,
    CLASSROOM_API_PROXY_TOKEN: BRIDGE_SECRET,
    CLASSROOM_API_ALLOWED_ORIGIN: ORIGIN,
    CLOUDFLARE_ACCESS_TEAM_DOMAIN: ISSUER,
    CLOUDFLARE_ACCESS_AUD: AUDIENCE,
    CLASSROOM_API: {
      async fetch(request) {
        upstreamRequests.push(request);
        return upstream ? upstream(request) : Response.json({ value: "observed-only" });
      },
    },
    ...overrides,
  };
  const proxy = createMonitoringProxy({
    fetchJwks: async (url, init) => {
      jwksRequests.push({ url: String(url), init });
      assert.equal(String(url), `${ISSUER}/cdn-cgi/access/certs`);
      return jwks ? jwks(url, init) : Response.json({ keys: [jwk] });
    },
  });
  return { env, proxy, upstreamRequests, jwksRequests };
}

async function browserRequest(path = `${BASE}/analytics`, {
  method = "GET", token, headers: overrides = {}, body, origin = ORIGIN, signal,
} = {}) {
  const headers = new Headers();
  if (token !== null) headers.set("Cf-Access-Jwt-Assertion", token ?? await assertion());
  if (method === "POST") {
    headers.set("Origin", origin);
    headers.set("Content-Type", "application/json");
  }
  for (const [key, value] of Object.entries(overrides)) {
    if (value === null) headers.delete(key);
    else headers.set(key, value);
  }
  return new Request(`${origin}${path}`, {
    method, headers, signal,
    ...(body !== undefined ? { body } : {}),
    ...(body instanceof ReadableStream ? { duplex: "half" } : {}),
  });
}

test("camera JPEG uses verified Access and the configured private bridge with no caching", async () => {
  const jpeg = new Uint8Array([0xff, 0xd8, 1, 2, 0xff, 0xd9]);
  const h = harness({ upstream: () => new Response(jpeg, { headers: {
    "Content-Type": "image/jpeg", "Set-Cookie": "private-camera-cookie",
    "X-Classroom-Proxy-Token": BRIDGE_SECRET, "Access-Control-Allow-Origin": "*",
  } }) });
  const response = await h.proxy(await browserRequest(CAMERA_PATH), h.env);
  assert.equal(response.status, 200);
  assert.deepEqual(new Uint8Array(await response.arrayBuffer()), jpeg);
  assert.equal(h.upstreamRequests[0].url, `http://host.docker.internal:8000${CAMERA_PATH}`);
  assert.deepEqual([...h.upstreamRequests[0].headers.keys()].sort(), ["accept", "x-classroom-proxy-token"]);
  assert.equal(h.upstreamRequests[0].headers.get("Accept"), "image/jpeg");
  assert.equal(response.headers.get("Cache-Control"), "no-store");
  assert.deepEqual([...response.headers.keys()].sort(), ["cache-control", "content-type", "x-content-type-options"]);
});

test("camera requires Access and rejects browser source overrides and other classrooms", async () => {
  const h = harness();
  assert.equal((await h.proxy(await browserRequest(CAMERA_PATH, { token: null }), h.env)).status, 401);
  assert.equal((await h.proxy(await browserRequest(`${CAMERA_PATH}?source=rtsp://other`), h.env)).status, 400);
  assert.equal((await h.proxy(await browserRequest(CAMERA_PATH, { method: "POST", body: "{}" }), h.env)).status, 405);
  assert.equal((await h.proxy(await browserRequest("/api/v1/classrooms/00000000-0000-4000-8000-000000000001/camera/frame"), h.env)).status, 404);
  assert.equal(h.upstreamRequests.length, 0);
});

for (const [name, contentType, body] of [
  ["HTML", "text/html", "<script>bad</script>"],
  ["JSON on a successful camera response", "application/json", "{}"],
  ["incomplete JPEG", "image/jpeg", new Uint8Array([0xff, 0xd8, 1, 2])],
  ["oversized JPEG", "image/jpeg", new Uint8Array(2 * 1024 * 1024 + 1)],
]) {
  test(`camera rejects ${name}`, async () => {
    const h = harness({ upstream: () => new Response(body, { headers: { "Content-Type": contentType } }) });
    const response = await h.proxy(await browserRequest(CAMERA_PATH), h.env);
    assert.equal(response.status, 502);
    assert.equal(response.headers.get("Content-Type"), "application/json");
    assert.ok(!(await response.text()).includes(BRIDGE_SECRET));
  });
}

test("camera unavailable JSON remains a bounded error, and JPEG is forbidden on other routes", async () => {
  const h = harness({ upstream: () => Response.json({ detail: "Camera preview is unavailable." }, { status: 503 }) });
  assert.equal((await h.proxy(await browserRequest(CAMERA_PATH), h.env)).status, 503);
  const invalid = harness({ upstream: () => new Response(new Uint8Array([0xff, 0xd8, 0xff, 0xd9]), {
    headers: { "Content-Type": "image/jpeg" },
  }) });
  assert.equal((await invalid.proxy(await browserRequest(), invalid.env)).status, 502);
});

test("camera stream forwards the first bytes before the session ends and cancellation reaches upstream", async () => {
  const firstPart = new TextEncoder().encode("--classroom-frame\r\nContent-Type: image/jpeg\r\n");
  let cancelled = false;
  const source = new ReadableStream({
    start(controller) { controller.enqueue(firstPart); },
    cancel() { cancelled = true; },
  });
  const h = harness({ upstream: () => new Response(source, { headers: {
    "Content-Type": "multipart/x-mixed-replace; boundary=classroom-frame",
    "Set-Cookie": "private-camera-cookie", "X-Classroom-Proxy-Token": BRIDGE_SECRET,
    "Access-Control-Allow-Origin": "*",
  } }) });
  const response = await h.proxy(await browserRequest(STREAM_PATH), h.env);
  assert.equal(response.status, 200);
  assert.equal(h.upstreamRequests[0].url, `http://host.docker.internal:8000${STREAM_PATH}`);
  assert.equal(h.upstreamRequests[0].headers.get("Accept"), "multipart/x-mixed-replace");
  assert.equal(response.headers.get("Content-Type"), "multipart/x-mixed-replace; boundary=classroom-frame");
  assert.equal(response.headers.get("Cache-Control"), "no-store, no-transform");
  assert.deepEqual([...response.headers.keys()].sort(), ["cache-control", "content-type", "x-content-type-options"]);
  const reader = response.body.getReader();
  assert.deepEqual((await reader.read()).value, firstPart);
  await reader.cancel();
  assert.equal(cancelled, true);
});

for (const contentType of ["text/html", "image/jpeg", "application/json",
  "multipart/x-mixed-replace; boundary=untrusted", "multipart/x-mixed-replace"]) {
  test(`camera stream rejects unexpected MIME ${contentType}`, async () => {
    let cancelled = false;
    const source = new ReadableStream({ cancel() { cancelled = true; } });
    const h = harness({ upstream: () => new Response(source, { headers: { "Content-Type": contentType } }) });
    const response = await h.proxy(await browserRequest(STREAM_PATH), h.env);
    assert.equal(response.status, 502);
    assert.equal(cancelled, true);
  });
}

test("camera stream keeps the same authentication, origin, classroom, method and source scope", async () => {
  const h = harness();
  for (const [path, options, expected] of [
    [STREAM_PATH, { token: null }, 401],
    [STREAM_PATH, { token: await assertion({}, { key: otherPair.privateKey }) }, 401],
    [STREAM_PATH, { headers: { Origin: "https://attacker.example" } }, 403],
    [STREAM_PATH + "?source=rtsp://other", {}, 400],
    [STREAM_PATH, { method: "POST", body: "{}" }, 405],
    ["/api/v1/classrooms/00000000-0000-4000-8000-000000000001/camera/stream", {}, 404],
  ]) {
    assert.equal((await h.proxy(await browserRequest(path, options), h.env)).status, expected);
  }
  assert.equal(h.upstreamRequests.length, 0);
});

test("camera stream forwards bounded backend availability errors", async () => {
  const h = harness({ upstream: () => Response.json({ detail: "Camera preview is not available." }, { status: 503 }) });
  const response = await h.proxy(await browserRequest(STREAM_PATH), h.env);
  assert.equal(response.status, 503);
  assert.equal(response.headers.get("Cache-Control"), "no-store");
  assert.ok(!(await response.text()).includes(BRIDGE_SECRET));
});

for (const [method, path] of [
  ["GET", `${BASE}/telemetry/latest`],
  ["GET", `${BASE}/occupancy/current`],
  ["GET", `${BASE}/analytics?period=day&date=2026-10-26`],
  ["POST", `${BASE}/analytics/report`],
]) {
  test(`${method} ${path} reaches only the configured private service after valid Access auth`, async () => {
    const h = harness();
    const request = await browserRequest(path, {
      method,
      ...(method === "POST" ? { body: "{}", headers: { Authorization: `Bearer ${REPORT_TOKEN}` } } : {}),
    });
    const response = await h.proxy(request, h.env);
    assert.equal(response.status, 200);
    assert.equal(h.upstreamRequests.length, 1);
    assert.equal(h.jwksRequests.length, 1);
    assert.equal(h.upstreamRequests[0].url, `http://host.docker.internal:8000${path}`);
    assert.equal(h.upstreamRequests[0].method, method);
    assert.equal(h.upstreamRequests[0].headers.get("X-Classroom-Proxy-Token"), BRIDGE_SECRET);
    assert.equal(response.headers.get("Cache-Control"), "no-store");
    assert.equal((await response.json()).value, "observed-only");
  });
}

for (const path of [
  `/api/v1/classrooms/00000000-0000-4000-8000-000000000001/analytics`,
  `${BASE}/devices`, `${BASE}/urgency`, `${BASE}/environment-analysis`,
  `/api/v1/devices/00000000-0000-4000-8000-000000000001/commands`,
  `/api/v1/commands/00000000-0000-4000-8000-000000000001`,
  `${BASE}/analytics/`, `${BASE}/analytics/report/`, "/health/live", "/openapi.json",
]) {
  test(`unapproved path ${path} is rejected without contacting private services`, async () => {
    const h = harness();
    const response = await h.proxy(await browserRequest(path), h.env);
    assert.equal(response.status, 404);
    assert.equal(h.upstreamRequests.length, 0);
    assert.equal(h.jwksRequests.length, 0);
  });
}

for (const [method, path] of [
  ["POST", `${BASE}/analytics`], ["DELETE", `${BASE}/analytics`],
  ["HEAD", `${BASE}/analytics`], ["OPTIONS", `${BASE}/analytics`],
  ["GET", `${BASE}/analytics/report`], ["PUT", `${BASE}/analytics/report`],
]) {
  test(`unapproved method ${method} cannot reach ${path}`, async () => {
    const h = harness();
    const response = await h.proxy(await browserRequest(path, { method }), h.env);
    assert.equal(response.status, 405);
    assert.equal(h.upstreamRequests.length, 0);
  });
}

for (const [name, env] of [
  ["missing binding", { CLASSROOM_API: undefined }],
  ["missing token", { CLASSROOM_API_PROXY_TOKEN: undefined }],
  ["short token", { CLASSROOM_API_PROXY_TOKEN: "short" }],
  ["whitespace token", { CLASSROOM_API_PROXY_TOKEN: " ".repeat(40) }],
  ["embedded whitespace", { CLASSROOM_API_PROXY_TOKEN: `${BRIDGE_SECRET}\n` }],
  ["missing issuer", { CLOUDFLARE_ACCESS_TEAM_DOMAIN: undefined }],
  ["insecure issuer", { CLOUDFLARE_ACCESS_TEAM_DOMAIN: "http://classroom-test.cloudflareaccess.com" }],
  ["arbitrary JWKS host", { CLOUDFLARE_ACCESS_TEAM_DOMAIN: "https://attacker.example" }],
  ["issuer credentials", { CLOUDFLARE_ACCESS_TEAM_DOMAIN: "https://user:password@classroom-test.cloudflareaccess.com" }],
  ["issuer path", { CLOUDFLARE_ACCESS_TEAM_DOMAIN: `${ISSUER}/other` }],
  ["issuer query", { CLOUDFLARE_ACCESS_TEAM_DOMAIN: `${ISSUER}?token=private` }],
  ["issuer port", { CLOUDFLARE_ACCESS_TEAM_DOMAIN: `${ISSUER}:8443` }],
  ["missing audience", { CLOUDFLARE_ACCESS_AUD: undefined }],
  ["invalid audience", { CLOUDFLARE_ACCESS_AUD: "short" }],
]) {
  test(`${name} fails closed before JWKS or private API access`, async () => {
    const h = harness({ env });
    const response = await h.proxy(await browserRequest(), h.env);
    assert.equal(response.status, 503);
    assert.equal(h.upstreamRequests.length, 0);
    assert.equal(h.jwksRequests.length, 0);
  });
}

for (const [name, options] of [
  ["missing", { token: null }],
  ["malformed", { token: "looks-like-a-cloudflare-login" }],
  ["oversized", { token: "a".repeat(16_385) }],
]) {
  test(`${name} identity assertion cannot authenticate a request`, async () => {
    const h = harness();
    const response = await h.proxy(await browserRequest(undefined, options), h.env);
    assert.equal(response.status, 401);
    assert.equal(h.upstreamRequests.length, 0);
    assert.equal(h.jwksRequests.length, 0);
  });
}

for (const [name, claims, options] of [
  ["invalid signature", {}, { key: otherPair.privateKey }],
  ["wrong issuer", { iss: "https://other-team.cloudflareaccess.com" }],
  ["wrong audience", { aud: "another-application-audience-0123456789" }],
  ["expired token", { exp: Math.floor(Date.now() / 1000) - 60 }],
  ["not yet valid token", { nbf: Math.floor(Date.now() / 1000) + 300 }],
  ["missing expiry", {}, { omit: ["exp"] }],
  ["missing issue time", {}, { omit: ["iat"] }],
  ["missing subject", {}, { omit: ["sub"] }],
  ["unexpected algorithm", {}, { key: new Uint8Array(32).fill(1), alg: "HS256" }],
]) {
  test(`${name} is rejected cryptographically with zero upstream requests`, async () => {
    const h = harness();
    const response = await h.proxy(await browserRequest(undefined, {
      token: await assertion(claims, options),
    }), h.env);
    assert.equal(response.status, 401);
    assert.equal(h.upstreamRequests.length, 0);
  });
}

test("JWKS lookup failure has a generic response and no private API request", async () => {
  const h = harness({ jwks: () => { throw new Error(`sensitive failure ${BRIDGE_SECRET}`); } });
  const response = await h.proxy(await browserRequest(), h.env);
  assert.equal(response.status, 401);
  assert.equal(h.upstreamRequests.length, 0);
  assert.ok(!(await response.text()).includes(BRIDGE_SECRET));
});

test("verified signing keys are cached across requests in one proxy instance", async () => {
  const h = harness();
  assert.equal((await h.proxy(await browserRequest(), h.env)).status, 200);
  assert.equal((await h.proxy(await browserRequest(), h.env)).status, 200);
  assert.equal(h.upstreamRequests.length, 2);
  assert.equal(h.jwksRequests.length, 1);
});

test("forged bridge, identity, forwarding and cookie headers are removed from upstream GET", async () => {
  const h = harness();
  const response = await h.proxy(await browserRequest(undefined, { headers: {
    "X-Classroom-Proxy-Token": "forged-browser-value",
    "X-Forwarded-For": "127.0.0.1", "X-Real-IP": "127.0.0.1",
    Forwarded: "for=127.0.0.1", Cookie: "CF_Authorization=not-for-backend",
    "Cf-Access-Authenticated-User-Email": "forged@example.com",
    Authorization: `Bearer ${REPORT_TOKEN}`, "X-Upstream-URL": "http://attacker.example",
  } }), h.env);
  assert.equal(response.status, 200);
  const forwarded = h.upstreamRequests[0];
  assert.deepEqual([...forwarded.headers.keys()].sort(), ["accept", "x-classroom-proxy-token"]);
  assert.equal(forwarded.headers.get("X-Classroom-Proxy-Token"), BRIDGE_SECRET);
  assert.equal(forwarded.headers.get("Authorization"), null);
  assert.equal(new URL(forwarded.url).hostname, "host.docker.internal");
});

test("AI report keeps only its report bearer token and bounded JSON body", async () => {
  const h = harness();
  const body = JSON.stringify({ period: "day", date: "2026-10-26", refresh: true });
  const response = await h.proxy(await browserRequest(`${BASE}/analytics/report`, {
    method: "POST", body,
    headers: { Authorization: `Bearer ${REPORT_TOKEN}`, Cookie: "session=private-browser-cookie" },
  }), h.env);
  assert.equal(response.status, 200);
  const forwarded = h.upstreamRequests[0];
  assert.equal(await forwarded.text(), body);
  assert.equal(forwarded.headers.get("Authorization"), `Bearer ${REPORT_TOKEN}`);
  assert.equal(forwarded.headers.get("Cookie"), null);
  assert.equal(forwarded.headers.get("Content-Type"), "application/json");
});

for (const [name, headers] of [
  ["missing Origin", { Origin: null }],
  ["foreign Origin", { Origin: "https://attacker.example" }],
  ["opaque Origin", { Origin: "null" }],
]) {
  test(`AI report rejects ${name} before private service access`, async () => {
    const h = harness();
    const response = await h.proxy(await browserRequest(`${BASE}/analytics/report`, {
      method: "POST", body: "{}", headers,
    }), h.env);
    assert.equal(response.status, 403);
    assert.equal(h.upstreamRequests.length, 0);
  });
}

test("an unexpected website origin is denied even with a valid identity token", async () => {
  const h = harness();
  const response = await h.proxy(await browserRequest(undefined, {
    origin: "https://attacker.example",
  }), h.env);
  assert.equal(response.status, 403);
  assert.equal(h.upstreamRequests.length, 0);
});

test("GET requests with a foreign Origin are denied", async () => {
  const h = harness();
  const response = await h.proxy(await browserRequest(undefined, {
    headers: { Origin: "https://attacker.example" },
  }), h.env);
  assert.equal(response.status, 403);
  assert.equal(h.upstreamRequests.length, 0);
});

for (const [name, headers] of [
  ["missing content type", { "Content-Type": null }],
  ["HTML content type", { "Content-Type": "text/html" }],
]) {
  test(`AI report rejects ${name}`, async () => {
    const h = harness();
    const response = await h.proxy(await browserRequest(`${BASE}/analytics/report`, {
      method: "POST", body: "{}", headers,
    }), h.env);
    assert.equal(response.status, 415);
    assert.equal(h.upstreamRequests.length, 0);
  });
}

test("AI report enforces its 4096-byte limit on actual streamed bytes", async () => {
  const h = harness();
  const stream = new ReadableStream({
    start(controller) {
      controller.enqueue(new Uint8Array(2048).fill(32));
      controller.enqueue(new Uint8Array(2049).fill(32));
      controller.close();
    },
  });
  const response = await h.proxy(await browserRequest(`${BASE}/analytics/report`, {
    method: "POST", body: stream,
  }), h.env);
  assert.equal(response.status, 413);
  assert.equal(h.upstreamRequests.length, 0);
});

test("AI report body at the byte limit remains accepted", async () => {
  const h = harness();
  const response = await h.proxy(await browserRequest(`${BASE}/analytics/report`, {
    method: "POST", body: " ".repeat(4094) + "{}",
  }), h.env);
  assert.equal(response.status, 200);
  assert.equal((await h.upstreamRequests[0].arrayBuffer()).byteLength, 4096);
});

test("an unreadable report stream gives a bounded client error without exposing its exception", async () => {
  const h = harness();
  const stream = new ReadableStream({ start(controller) {
    controller.error(new Error(`private stream failure ${BRIDGE_SECRET}`));
  } });
  const response = await h.proxy(await browserRequest(`${BASE}/analytics/report`, {
    method: "POST", body: stream,
  }), h.env);
  assert.ok([400, 408, 413].includes(response.status));
  assert.equal(h.upstreamRequests.length, 0);
  assert.ok(!(await response.text()).includes(BRIDGE_SECRET));
});

for (const [name, upstream] of [
  ["redirect", () => new Response(null, { status: 302, headers: {
    Location: "https://attacker.example", "Content-Type": "application/json",
  } })],
  ["HTML", () => new Response("<script>not a JSON API response</script>", {
    headers: { "Content-Type": "text/html" },
  })],
  ["JSON text hidden in another MIME's parameter", () => new Response("<p>wrong MIME</p>", {
    headers: { "Content-Type": "text/html; note=application/json" },
  })],
]) {
  test(`private API ${name} response is replaced by a generic gateway error`, async () => {
    const h = harness({ upstream });
    const response = await h.proxy(await browserRequest(), h.env);
    assert.equal(response.status, 502);
    assert.equal(response.headers.get("Location"), null);
    assert.equal(response.headers.get("Content-Type"), "application/json");
    assert.ok(!(await response.text()).includes("attacker.example"));
  });
}

test("only JSON/cache/retry headers survive and bridge credentials never enter the response", async () => {
  const h = harness({ upstream: () => Response.json({ detail: "Report access token required" }, {
    status: 401,
    headers: {
      "Set-Cookie": "private-origin-session=hidden", "X-Classroom-Proxy-Token": BRIDGE_SECRET,
      Authorization: BRIDGE_SECRET, "Cf-Access-Jwt-Assertion": "upstream-private-identity",
      "Retry-After": "30", "Access-Control-Allow-Origin": "*",
    },
  }) });
  const response = await h.proxy(await browserRequest(), h.env);
  assert.equal(response.status, 401);
  assert.equal(response.headers.get("Cache-Control"), "no-store");
  assert.equal(response.headers.get("Retry-After"), "30");
  assert.deepEqual([...response.headers.keys()].sort(), ["cache-control", "content-type", "retry-after"]);
  assert.ok(!(await response.text()).includes(BRIDGE_SECRET));
});

test("upstream failure does not expose exception details or secrets", async () => {
  const h = harness({ upstream: () => { throw new Error(`private network failure ${BRIDGE_SECRET}`); } });
  const response = await h.proxy(await browserRequest(), h.env);
  assert.equal(response.status, 502);
  assert.ok(!(await response.text()).includes(BRIDGE_SECRET));
});

test("upstream requests have a cancellation signal and redirects stay manual", async () => {
  const h = harness();
  const response = await h.proxy(await browserRequest(), h.env);
  assert.equal(response.status, 200);
  assert.ok(h.upstreamRequests[0].signal instanceof AbortSignal);
  assert.equal(h.upstreamRequests[0].signal.aborted, false);
  assert.equal(h.upstreamRequests[0].redirect, "manual");
});

const RELAYS = [
  "d7958a13-4c75-47e8-8b5e-4f4e00000027",
  "d7958a13-4c75-47e8-8b5e-4f4e00000012",
  "d7958a13-4c75-47e8-8b5e-4f4e00000014",
];
const COMMAND_PATH = `/api/v1/devices/${RELAYS[1]}/commands`;
const POWER = { action: "set_power", value: { on: true }, reason: "Operator request" };
async function controlRequest(path = COMMAND_PATH, options = {}) {
  return browserRequest(path, {
    method: "POST", token: await assertion({ email: OPERATOR }),
    body: JSON.stringify(POWER), ...options,
    headers: { "Idempotency-Key": "unique-test-key", ...options.headers },
  });
}

for (const path of [`${BASE}/devices`, `${BASE}/urgency`, "/api/v1/commands/00000000-0000-0000-0000-000000000001"]) {
  test(`authorized operator can read ${path}`, async () => {
    const h = harness({ env: CONTROL_ENV });
    const response = await h.proxy(await controlRequest(path, { method: "GET", body: undefined }), h.env);
    assert.equal(response.status, 200);
    assert.equal(h.upstreamRequests[0].headers.get("X-Classroom-Operator-Email"), OPERATOR);
    assert.equal(h.upstreamRequests[0].headers.get("Idempotency-Key"), null);
  });
}

for (const relay of RELAYS) {
  for (const command of [POWER, { ...POWER, value: { on: false } },
    { action: "set_mode", value: { mode: "auto" } }, { action: "set_mode", value: { mode: "manual" } }]) {
    test(`authorized relay ${relay} accepts ${JSON.stringify(command)}`, async () => {
      const h = harness({ env: CONTROL_ENV });
      const response = await h.proxy(await controlRequest(`/api/v1/devices/${relay}/commands`, {
        body: JSON.stringify(command),
      }), h.env);
      assert.equal(response.status, 200);
      assert.equal(h.upstreamRequests[0].headers.get("Idempotency-Key"), "unique-test-key");
      assert.deepEqual(await h.upstreamRequests[0].json(), command);
    });
  }
}

for (const email of [undefined, null, 123, "other@example.com", "operator@example.com,other@example.com"]) {
  test(`signed but unauthorized email ${email} cannot control hardware`, async () => {
    const h = harness({ env: CONTROL_ENV });
    const response = await h.proxy(await controlRequest(undefined, {
      token: await assertion({ email }), headers: { "X-Classroom-Operator-Email": OPERATOR },
    }), h.env);
    assert.equal(response.status, 403);
    assert.equal(h.upstreamRequests.length, 0);
  });
}

test("control identity comes only from the verified claim and fresh trusted headers", async () => {
  const h = harness({ env: CONTROL_ENV });
  const response = await h.proxy(await controlRequest(undefined, {
    token: await assertion({ email: OPERATOR.toUpperCase() }),
    headers: {
      "X-Classroom-Operator-Email": "forged@example.com", "X-Classroom-Proxy-Token": "forged",
      Cookie: "browser-secret", Authorization: "Bearer browser-secret", "X-Forwarded-For": "127.0.0.1",
    },
  }), h.env);
  assert.equal(response.status, 200);
  const headers = h.upstreamRequests[0].headers;
  assert.deepEqual([...headers.keys()].sort(), ["accept", "content-type", "idempotency-key",
    "x-classroom-operator-email", "x-classroom-proxy-token"]);
  assert.equal(headers.get("X-Classroom-Operator-Email"), OPERATOR);
  assert.equal(headers.get("X-Classroom-Proxy-Token"), BRIDGE_SECRET);
});

test("control JWT signature is verified before any private request", async () => {
  const h = harness({ env: CONTROL_ENV });
  const response = await h.proxy(await controlRequest(undefined, {
    token: await assertion({ email: OPERATOR }, { key: otherPair.privateKey }),
  }), h.env);
  assert.equal(response.status, 401);
  assert.equal(h.upstreamRequests.length, 0);
});

test("missing operator configuration fails closed while monitoring still works", async () => {
  const h = harness({ env: { CLASSROOM_REMOTE_CONTROL_ENABLED: "true" } });
  assert.equal((await h.proxy(await controlRequest(), h.env)).status, 503);
  assert.equal(h.upstreamRequests.length, 0);
  assert.equal((await h.proxy(await browserRequest(), h.env)).status, 200);
});

for (const key of [null, "", "has space", "x".repeat(201)]) {
  test(`invalid command idempotency key ${String(key).slice(0, 20)} is rejected`, async () => {
    const h = harness({ env: CONTROL_ENV });
    const response = await h.proxy(await controlRequest(undefined, { headers: { "Idempotency-Key": key } }), h.env);
    assert.equal(response.status, 400);
    assert.equal(h.upstreamRequests.length, 0);
  });
}

for (const body of ["{", "null", "[]", JSON.stringify({ action: "set_speed", value: { speed: 2 } }),
  JSON.stringify({ ...POWER, value: { on: "true" } }),
  JSON.stringify({ ...POWER, value: { on: true, brightness_pct: 50 } }),
  JSON.stringify({ action: "set_mode", value: { mode: "scheduled" } })]) {
  test(`unsupported command body ${body} never reaches the private service`, async () => {
    const h = harness({ env: CONTROL_ENV });
    const response = await h.proxy(await controlRequest(undefined, { body }), h.env);
    assert.equal(response.status, 400);
    assert.equal(h.upstreamRequests.length, 0);
  });
}

for (const origin of [null, "https://attacker.example", "null"]) {
  test(`command Origin ${origin} is rejected`, async () => {
    const h = harness({ env: CONTROL_ENV });
    const response = await h.proxy(await controlRequest(undefined, { headers: { Origin: origin } }), h.env);
    assert.equal(response.status, 403);
    assert.equal(h.upstreamRequests.length, 0);
  });
}

for (const [path, status] of [
  [`${BASE}/urgency`, 405],
  ["/api/v1/devices/00000000-0000-0000-0000-000000000001/commands", 404],
  [COMMAND_PATH + "?override=true", 400],
  ["/api/v1/classrooms/00000000-0000-0000-0000-000000000001/devices", 404],
]) {
  test(`remote control route scope rejects ${path}`, async () => {
    const h = harness({ env: CONTROL_ENV });
    assert.equal((await h.proxy(await controlRequest(path), h.env)).status, status);
    assert.equal(h.upstreamRequests.length, 0);
  });
}
