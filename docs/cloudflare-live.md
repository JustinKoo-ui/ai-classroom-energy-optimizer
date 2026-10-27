# Cloudflare monitoring and relay controls

The Nexergy Worker serves the frontend and proxies a narrow same-origin API
to FastAPI through Workers VPC and a Cloudflare Tunnel. A custom domain is
not required for the existing `workers.dev` website. The browser never
connects to MQTT or receives the private bridge secret.

```text
Browser -> HTTPS + Cloudflare Access -> Nexergy Worker
        -> Workers VPC -> QUIC tunnel -> private FastAPI
        -> deterministic policy -> persisted command -> MQTT -> ESP32 acknowledgement
```

This is the seeded, single-classroom, low-voltage prototype. Aggregate demo
panels remain simulated. Supported physical controls are ON/OFF and
Auto/Manual; fan speed, brightness and AC setpoints are unsupported.

## Cloudflare configuration

1. Keep Cloudflare Access enabled for **All traffic** on the `nexergy` Worker.
   The current policy requires membership of the Cloudflare account.
2. Keep the `classroom-backend` tunnel running on the backend computer. Workers
   VPC requires QUIC connectivity; an HTTP/2 fallback is insufficient.
3. The VPC service uses HTTP host `host.docker.internal`, HTTP port `8000`,
   through that tunnel. Do not publish the MQTT broker or FastAPI directly.
4. `frontend/wrangler.jsonc` binds `CLASSROOM_API` to that VPC service. It also
   configures the exact website origin, classroom, Access team URL and
   application audience. Obtain these from the actual Access application;
   never disable JWT verification to work around a mismatch.

The current deployment uses
`https://nexergy.justin58094.workers.dev` and VPC service
`01a177f7-2878-7230-9ed8-3b7aa5904c38`. Change the binding and origin when
deploying to another account.

## Backend and private operator configuration

Keep existing database, MQTT and device settings in ignored `backend/.env`.
The backend hardware gate must be enabled:

```text
LOCAL_HARDWARE_CONTROL_ENABLED=true
```

From `frontend`, run the setup helper with your approved Cloudflare login
email (replace the example):

```powershell
node scripts/configure-private-api.mjs --enable-remote-control --operator-email operator@example.com
```

The helper creates or reuses a random bridge secret, writes backend bridge
and remote-operator settings, and uploads matching Worker secrets without
displaying them. Restart/reload FastAPI after changing its environment.

The corresponding settings are:

| Location | Setting | Purpose |
|---|---|---|
| Backend | `CLASSROOM_API_PROXY_ENABLED=true` | Enable the authenticated private bridge |
| Backend + Worker secret | `CLASSROOM_API_PROXY_TOKEN` | Server-to-server authentication |
| Backend + Worker var | `CLASSROOM_API_PROXY_CLASSROOM_ID` / `CLASSROOM_API_CLASSROOM_ID` | Scope monitoring to the configured classroom |
| Backend + Worker var | `CLASSROOM_REMOTE_CONTROL_ENABLED=true` | Additional relay-control opt-in |
| Backend + Worker secret | `CLASSROOM_REMOTE_CONTROL_ALLOWED_EMAILS` | Approved signed-in operators |
| Worker vars | `CLOUDFLARE_ACCESS_TEAM_DOMAIN`, `CLOUDFLARE_ACCESS_AUD` | Trusted JWT issuer and audience |

The email allowlists support comma-separated values when configured
manually. The helper replaces them with the single supplied operator.
Default backend settings leave both bridge and remote controls disabled.
AI provider keys and the separate analyst report token stay in the backend;
report generation still requires its existing report bearer token.

## Production frontend and deployment

Use ignored `frontend/.env.production.local`, keeping local development
settings in `.env.local`:

```text
NEXT_PUBLIC_API_BASE_URL=
NEXT_PUBLIC_CLASSROOM_ID=26e97d46-7a34-4439-922a-83684bda557d
NEXT_PUBLIC_USE_MOCK_DATA=true
NEXT_PUBLIC_USE_LIVE_OCCUPANCY=true
NEXT_PUBLIC_ENABLE_HARDWARE_CONTROLS=false
NEXT_PUBLIC_ENABLE_REMOTE_HARDWARE_CONTROLS=true
NEXT_PUBLIC_HARDWARE_CONTROL_ORIGIN=https://nexergy.justin58094.workers.dev
```

An empty API base selects the same-origin Worker proxy. The frontend remote
flag works only on the exact configured HTTPS origin. Keep mock data enabled
for aggregate panels whose full live dashboard contract is not implemented.
All `NEXT_PUBLIC_*` values are public; never put secrets there.

```powershell
pnpm exec tsc --noEmit
pnpm run lint
pnpm run test:analytics
pnpm run test:proxy
pnpm run test:hardware
pnpm run build
pnpm run deploy -- --skip-build
```

Wrangler must be authenticated to the intended account. The production build
must retain the VPC binding and remote-control flag. Local development omits
the VPC binding and continues using the loopback API. Same-origin proxying
does not require adding a public backend CORS origin.

## Using and checking the deployed controls

1. Open the deployed website, sign in through Cloudflare Access with the
   configured operator account, and refresh after a new deployment.
2. Confirm the banner says **Authenticated Cloudflare ESP32 controls** and
   the relevant device is online. Offline, stale, faulted or pending devices
   have locked controls.
3. Select **Manual**, wait for the ESP32 acknowledgement, then use the power
   switch. Select **Auto** to restore the existing sensor/occupancy policy.
4. Keep the backend computer, database, MQTT broker, tunnel and ESP32 running.

The dashboard sends one request per change and polls its command status.
`queued` or `accepted` does not mean applied. An `applied` acknowledgement is
controller-reported state, not independent physical feedback. Remote urgency
activation is intentionally unavailable; its status remains readable.

Unsigned public requests should reach the Access login. Missing operator
authorization produces a control error and cannot fall back to simulated
success. A gateway error indicates the backend/tunnel may be unreachable.
The backend and proxy regression suites use mocks and do not actuate devices;
a signed-in, physical indicator check remains a separate manual check.

## Optional live camera preview

Set `CAMERA_PREVIEW_ENABLED=true` and `CAMERA_SOURCE` to the camera's RTSP URL
in ignored `backend/.env`, then install the updated backend dependencies and
restart FastAPI. Keep the RTSP URL and camera password out of public variables.
The backend uses the FFmpeg executable bundled by `imageio-ffmpeg`, so a separate
system FFmpeg installation is not required. It must be on a network that can
reach the camera. The CV worker can keep using its own camera setting.

The dashboard's **Live camera** panel starts after **Start preview**. It provides
a silent preview at up to 15 frames per second through the same Access, VPC
and tunnel connection as monitoring. A continuous MJPEG response avoids a
separate HTTP round trip for every frame. Sessions reconnect every 60 seconds
to recheck authentication; slow viewers receive the latest available frame.
It pauses in a hidden tab, clears frames on errors, and closes the connection
after **Stop preview**. The backend stops decoding
after about 15 seconds with no viewers. Frames remain in memory only; no video
is recorded. The preview is an additional RTSP connection to the camera.

Deploy the updated Worker as well as restarting FastAPI: both sides explicitly
allow `/api/v1/classrooms/{configured_id}/camera/stream` and the single-frame
`/api/v1/classrooms/{configured_id}/camera/frame` endpoint. No camera port
forwarding or separate public video endpoint is needed.

Official references: [Workers VPC setup](https://developers.cloudflare.com/workers-vpc/get-started/),
[VPC service configuration](https://developers.cloudflare.com/workers-vpc/configuration/vpc-services/),
[Access for Workers](https://developers.cloudflare.com/workers/configuration/cloudflare-access/).
