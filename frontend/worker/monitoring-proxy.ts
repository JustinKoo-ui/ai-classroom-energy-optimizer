import { createRemoteJWKSet, customFetch, jwtVerify } from "jose";
import type { FetchImplementation } from "jose";

interface PrivateService {
  fetch(request: Request): Promise<Response>;
}

export interface MonitoringEnv {
  CLASSROOM_API?: PrivateService;
  CLASSROOM_API_PROXY_TOKEN?: string;
  CLASSROOM_API_CLASSROOM_ID?: string;
  CLASSROOM_API_ALLOWED_ORIGIN?: string;
  CLOUDFLARE_ACCESS_TEAM_DOMAIN?: string;
  CLOUDFLARE_ACCESS_AUD?: string;
  CLASSROOM_REMOTE_CONTROL_ENABLED?: string;
  CLASSROOM_REMOTE_CONTROL_ALLOWED_EMAILS?: string;
}

const MAX_REPORT_BYTES = 4096;
const MAX_CAMERA_FRAME_BYTES = 2 * 1024 * 1024;
const CAMERA_STREAM_TYPE = "multipart/x-mixed-replace; boundary=classroom-frame";
const NO_STORE = { "Cache-Control": "no-store" };
const RELAY_IDS = new Set([
  "d7958a13-4c75-47e8-8b5e-4f4e00000027",
  "d7958a13-4c75-47e8-8b5e-4f4e00000012",
  "d7958a13-4c75-47e8-8b5e-4f4e00000014",
]);
const UUID_PATTERN = "[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}";

function error(status: number, detail: string): Response {
  return Response.json({ detail }, { status, headers: NO_STORE });
}

function accessConfiguration(env: MonitoringEnv) {
  try {
    const issuer = new URL(env.CLOUDFLARE_ACCESS_TEAM_DOMAIN ?? "");
    const audience = env.CLOUDFLARE_ACCESS_AUD ?? "";
    if (issuer.protocol !== "https:" || issuer.port || issuer.username || issuer.password
      || !/^[a-z0-9-]+\.cloudflareaccess\.com$/.test(issuer.hostname)
      || issuer.pathname !== "/" || issuer.search || issuer.hash
      || !/^[a-zA-Z0-9_-]{16,256}$/.test(audience)) return null;
    return { issuer: issuer.origin, audience };
  } catch {
    return null;
  }
}

async function reportBody(request: Request): Promise<Uint8Array | null> {
  const reader = request.body?.getReader();
  if (!reader) return new Uint8Array();
  const chunks: Uint8Array[] = [];
  let size = 0;
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    size += value.byteLength;
    if (size > MAX_REPORT_BYTES) {
      await reader.cancel();
      return null;
    }
    chunks.push(value);
  }
  const body = new Uint8Array(size);
  let offset = 0;
  for (const chunk of chunks) {
    body.set(chunk, offset);
    offset += chunk.length;
  }
  return body;
}

async function cameraFrame(response: Response): Promise<Uint8Array | null> {
  const reader = response.body?.getReader();
  if (!reader) return null;
  const chunks: Uint8Array[] = [];
  let size = 0;
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    size += value.byteLength;
    if (size > MAX_CAMERA_FRAME_BYTES) {
      await reader.cancel();
      return null;
    }
    chunks.push(value);
  }
  const frame = new Uint8Array(size);
  let offset = 0;
  for (const chunk of chunks) {
    frame.set(chunk, offset);
    offset += chunk.length;
  }
  // Only a bounded, complete JPEG can leave the private camera bridge.
  if (size < 4 || frame[0] !== 0xff || frame[1] !== 0xd8
    || frame[size - 2] !== 0xff || frame[size - 1] !== 0xd9) return null;
  return frame;
}

export function createMonitoringProxy(options: { fetchJwks?: FetchImplementation } = {}) {
  const resolvers = new Map<string, ReturnType<typeof createRemoteJWKSet>>();

  return async function monitoringProxy(request: Request, env: MonitoringEnv): Promise<Response> {
    const url = new URL(request.url);
    const classroom = env.CLASSROOM_API_CLASSROOM_ID ?? "";
    const base = `/api/v1/classrooms/${classroom}`;
    const isCameraStream = url.pathname === `${base}/camera/stream`;
    const isCameraSnapshot = url.pathname === `${base}/camera/frame`;
    const isCamera = isCameraStream || isCameraSnapshot;
    const readPaths = new Set([`${base}/telemetry/latest`, `${base}/occupancy/current`, `${base}/analytics`, `${base}/camera/frame`, `${base}/camera/stream`]);
    const reportPath = `${base}/analytics/report`;
    const commandMatch = url.pathname.match(new RegExp(`^/api/v1/devices/(${UUID_PATTERN})/commands$`));
    const isCommand = commandMatch !== null && RELAY_IDS.has(commandMatch[1]);
    const isControl = url.pathname === `${base}/devices` || url.pathname === `${base}/urgency`
      || isCommand || new RegExp(`^/api/v1/commands/${UUID_PATTERN}$`).test(url.pathname);
    const controlEnabled = env.CLASSROOM_REMOTE_CONTROL_ENABLED === "true";
    if (!new RegExp(`^${UUID_PATTERN}$`).test(classroom)
      || (!readPaths.has(url.pathname) && url.pathname !== reportPath && !(isControl && controlEnabled))) {
      return error(404, "This API route is not available through the monitoring website.");
    }
    const expectedMethod = url.pathname === reportPath || isCommand ? "POST" : "GET";
    if (request.method !== expectedMethod) return error(405, "This API method is not allowed.");
    if (isControl && url.search) return error(400, "Control routes do not accept query parameters.");
    if (isCamera && url.search) return error(400, "Camera preview does not accept query parameters.");

    const access = accessConfiguration(env);
    const secret = env.CLASSROOM_API_PROXY_TOKEN;
    if (!access || !env.CLASSROOM_API || !secret || secret.trim().length < 32 || /\s/.test(secret)) {
      return error(503, "The private monitoring connection is not configured.");
    }
    const operators = new Set((env.CLASSROOM_REMOTE_CONTROL_ALLOWED_EMAILS ?? "")
      .split(",").map((email) => email.trim().toLowerCase()).filter(Boolean));
    if (isControl && operators.size === 0) return error(503, "Remote control operators are not configured.");
    const allowedOrigin = env.CLASSROOM_API_ALLOWED_ORIGIN;
    if (!allowedOrigin || url.origin !== allowedOrigin) return error(403, "This website origin is not allowed.");
    const origin = request.headers.get("Origin");
    if ((origin && origin !== allowedOrigin) || (request.method === "POST" && origin !== allowedOrigin)) {
      return error(403, "This request origin is not allowed.");
    }

    // Static Assets use an internal router which may not forward ctx.access.
    // Verify the assertion cryptographically against a configured issuer and AUD.
    const assertion = request.headers.get("Cf-Access-Jwt-Assertion") ?? "";
    if (assertion.length > 16_384 || !/^[\w-]+\.[\w-]+\.[\w-]+$/.test(assertion)) {
      return error(401, "Sign in through Cloudflare Access to use monitoring.");
    }
    let operatorEmail: string | undefined;
    try {
      let resolver = resolvers.get(access.issuer);
      if (!resolver) {
        resolver = createRemoteJWKSet(new URL(`${access.issuer}/cdn-cgi/access/certs`), {
          timeoutDuration: 5000,
          cacheMaxAge: 600_000,
          cooldownDuration: 30_000,
          ...(options.fetchJwks ? { [customFetch]: options.fetchJwks } : {}),
        });
        if (resolvers.size >= 8) resolvers.clear();
        resolvers.set(access.issuer, resolver);
      }
      const { payload } = await jwtVerify(assertion, resolver, {
        algorithms: ["RS256"],
        issuer: access.issuer,
        audience: access.audience,
        requiredClaims: ["iss", "aud", "sub", "iat", "exp"],
        clockTolerance: 5,
      });
      if (isControl) {
        const email = typeof payload.email === "string" ? payload.email.trim().toLowerCase() : "";
        if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email) || !operators.has(email)) {
          return error(403, "Your Cloudflare account is not authorized for relay controls.");
        }
        operatorEmail = email;
      }
    } catch {
      return error(401, "Cloudflare Access authentication could not be verified. Sign in again.");
    }

    let body: Uint8Array | undefined;
    const requestLabel = isCommand ? "Control" : "AI report";
    if (request.method === "POST") {
      if (request.headers.get("Content-Type")?.split(";")[0].trim().toLowerCase() !== "application/json") {
        return error(415, `${requestLabel} requests must use application/json.`);
      }
      let bytes: Uint8Array | null;
      try {
        bytes = await reportBody(request);
      } catch {
        return error(400, `The ${requestLabel.toLowerCase()} request body could not be read.`);
      }
      if (bytes === null) return error(413, `The ${requestLabel.toLowerCase()} request is too large.`);
      body = bytes;
      if (isCommand) {
        try {
          const command = JSON.parse(new TextDecoder().decode(bytes));
          const value = command?.value;
          const keys = value && typeof value === "object" && !Array.isArray(value) ? Object.keys(value) : [];
          const power = command?.action === "set_power" && keys.length === 1
            && keys[0] === "on" && typeof value.on === "boolean";
          const mode = command?.action === "set_mode" && keys.length === 1
            && keys[0] === "mode" && ["auto", "manual"].includes(value.mode);
          if (!power && !mode) return error(400, "Relay controls support only ON/OFF and Auto/Manual mode.");
        } catch {
          return error(400, "The relay command must contain valid JSON.");
        }
      }
    }
    // Construct fresh headers: never trust browser-supplied bridge/forwarding
    // headers, cookies, identity assertions, or an upstream address from input.
    const headers = new Headers({
      Accept: isCameraStream ? "multipart/x-mixed-replace" : isCameraSnapshot ? "image/jpeg" : "application/json",
      "X-Classroom-Proxy-Token": secret,
    });
    if (operatorEmail) headers.set("X-Classroom-Operator-Email", operatorEmail);
    if (body) {
      headers.set("Content-Type", "application/json");
      if (isCommand) {
        const idempotency = request.headers.get("Idempotency-Key");
        if (!idempotency || idempotency.length > 200 || !/^[\x21-\x7e]+$/.test(idempotency)) {
          return error(400, "A valid Idempotency-Key is required for relay commands.");
        }
        headers.set("Idempotency-Key", idempotency);
      } else {
        const bearer = request.headers.get("Authorization");
        if (bearer) headers.set("Authorization", bearer);
      }
    }
    const upstreamUrl = new URL(`${url.pathname}${url.search}`, "http://host.docker.internal:8000");
    try {
      const response = await env.CLASSROOM_API.fetch(new Request(upstreamUrl, {
        method: request.method,
        headers,
        body: body ? new Uint8Array(body).buffer : undefined,
        redirect: "manual",
        // Camera sessions close after 60 seconds on the backend. Keep the
        // cancellation budget long enough to deliver the entire session.
        signal: AbortSignal.any([request.signal, AbortSignal.timeout(
          isCameraStream ? 75_000 : url.pathname === reportPath ? 90_000 : 15_000,
        )]),
      }));
      if (isCameraStream && response.status === 200) {
        if (!response.body || !/^multipart\/x-mixed-replace;\s*boundary="?classroom-frame"?$/i.test(
          response.headers.get("Content-Type")?.trim() ?? "",
        )) {
          await response.body?.cancel();
          return error(502, "The camera returned an unexpected stream response.");
        }
        // Forward as it arrives. Buffering the body would delay every frame
        // until the session ends and would grow memory for each viewer.
        return new Response(response.body, { headers: {
          "Cache-Control": "no-store, no-transform",
          "Content-Type": CAMERA_STREAM_TYPE,
          "X-Content-Type-Options": "nosniff",
        } });
      }
      if (isCameraSnapshot && response.status === 200) {
        if (response.headers.get("Content-Type")?.split(";")[0].trim().toLowerCase() !== "image/jpeg") {
          return error(502, "The camera returned an unexpected preview response.");
        }
        const frame = await cameraFrame(response);
        if (!frame) return error(502, "The camera returned an invalid preview frame.");
        return new Response(new Uint8Array(frame).buffer, { headers: {
          ...NO_STORE, "Content-Type": "image/jpeg", "X-Content-Type-Options": "nosniff",
        } });
      }
      if ((response.status >= 300 && response.status < 400)
        || response.headers.get("Content-Type")?.split(";")[0].trim().toLowerCase() !== "application/json") {
        return error(502, "The private API returned an unexpected response.");
      }
      const responseHeaders = new Headers({ ...NO_STORE, "Content-Type": "application/json" });
      const retryAfter = response.headers.get("Retry-After");
      if (retryAfter) responseHeaders.set("Retry-After", retryAfter);
      return new Response(response.body, { status: response.status, headers: responseHeaders });
    } catch {
      return error(502, "The private API could not be reached. Check the backend and tunnel connection.");
    }
  };
}
