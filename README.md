# AI Classroom Energy Optimizer

Project for monitoring classroom occupancy, comfort and
electrical energy, then issuing appliance commands through a deterministic
control policy.

This repository contains the architecture, protocol contracts, database
schema, shared types, environment telemetry, and an ESP32 ON/OFF control
prototype with local access and optional authenticated Cloudflare access.
The prototype is for low-voltage testing only; it does not support variable
fan speed, brightness, temperature setpoints, or measured power/energy.

## Core invariant

AI components may create observations, predictions and recommendations. They
must not publish appliance commands. Only the deterministic control service may
approve a command, and only the command dispatcher may publish it to MQTT.

## Repository layout

```text
ai-classroom-energy-optimizer/
|-- backend/                 FastAPI modular monolith and workers
|   |-- app/api/v1/          Versioned REST routes
|   |-- app/core/            Configuration, security, logging, lifecycle
|   |-- app/domain/          Domain rules and framework-free entities
|   |-- app/integrations/    MQTT and external service adapters
|   |-- app/repositories/    PostgreSQL persistence adapters
|   |-- app/schemas/         Pydantic boundary contracts
|   |-- app/services/        Policy, command, scheduling and analytics logic
|   `-- app/workers/         MQTT ingestion and scheduled background work
|-- frontend/                React dashboard with local/Cloudflare relay control
|   `-- src/types/           TypeScript wire contracts
|-- cv_service/              Roboflow detection + in-memory ByteTrack worker
|-- ml/                      Leave-within-60-seconds model training/inference
|-- firmware/esp32/          Sensor and appliance-controller firmware
|-- database/                PostgreSQL DDL and migrations
|-- docs/                    Architecture and protocol specifications
|-- infra/                   Local/deployment infrastructure definitions
|-- scripts/                 Development and operations helpers
|-- .env.example             Configuration contract
`-- CLAUDE.md                Engineering rules and system context
```

Empty source directories are intentional extension points for later increments.

## Start here

The additive runtime tracker, estimated-energy dashboard and optional AI
Environmental Analyst are documented in [docs/analytics.md](docs/analytics.md).
Existing databases need migration `004_runtime_history.sql`. Runtime and energy
analytics work without an AI key; existing demo energy panels remain simulated.

1. Read [docs/architecture.md](docs/architecture.md).
2. Read [docs/control-policy.md](docs/control-policy.md) before implementing
   any automation.
3. Review [docs/requirements-traceability.md](docs/requirements-traceability.md)
   for coverage of all 15 system objectives.
4. Apply [database/schema.sql](database/schema.sql) to a development PostgreSQL
   database.
5. Treat the Pydantic and TypeScript contracts as two representations of the
   documented wire formats; update both in the same change.

## Deploy the dashboard as a website

The frontend uses `MockDashboardService` for aggregate demo panels. Live
telemetry, runtime analytics and ON/OFF + Auto/Manual relay controls can use
the authenticated Cloudflare Worker and a private Workers VPC service. See
[the Cloudflare setup guide](docs/cloudflare-live.md) for the separate remote
control opt-in, operator allowlist and backend connection. Commands retain
the deterministic policy, persisted outbox and ESP32 acknowledgement path.

### Deploy to Cloudflare Workers

The frontend is configured for Cloudflare Workers. From the repository root,
authenticate with Cloudflare and deploy the frontend:

```powershell
Set-Location frontend
corepack enable
pnpm install --frozen-lockfile
npx wrangler login
pnpm run deploy
```

The `deploy` script builds the Worker with vinext and publishes it using
Wrangler. The mock dashboard is enabled by default, so no application
environment variables are required for the demo. Set any `NEXT_PUBLIC_*`
values before building if needed; these values are public and bundled into
browser JavaScript.

Do not put database passwords, JWT signing keys, MQTT credentials, Roboflow
keys or private certificates in `NEXT_PUBLIC_*` variables. Variables with that
prefix are bundled into browser JavaScript and are public.

### Connect a deployed backend later

When the full FastAPI API is implemented and hosted over HTTPS, configure these
frontend build-time variables:

```text
NEXT_PUBLIC_USE_MOCK_DATA=false
NEXT_PUBLIC_API_BASE_URL=https://api.example.com
NEXT_PUBLIC_CLASSROOM_ID=26e97d46-7a34-4439-922a-83684bda557d
```

The API must allow the deployed frontend origin in
`CORS_ALLOWED_ORIGINS` and support credentials if authentication is enabled.
The dashboard polls the API every five seconds by default. Deploy PostgreSQL,
the MQTT broker, the dispatcher and the FastAPI service separately; the browser
must never connect directly to MQTT. Use the deployment variables in
[.env.example](.env.example) as a server-side configuration checklist, but do
not commit a populated `.env` file.

### Local production check

Run this before deploying:

```powershell
Set-Location frontend
corepack enable
pnpm install --frozen-lockfile
pnpm run build
```

If dependencies were copied from another machine, remove `frontend/node_modules`
and run the install command again so native packages are rebuilt for the
current operating system.

## Planned incremental delivery

1. Infrastructure, database migrations and simulated MQTT devices.
2. Telemetry ingestion, current room state and REST-polled dashboard updates.
3. Local manual control through the deterministic policy and acknowledgement
   loop (implemented for relay ON/OFF; production authorization remains future
   work).
4. Occupancy detection/tracking and privacy validation (CV MQTT ingestion and
   local relay automation now implemented for the localhost prototype).
5. Comfort automation, vacancy handling and schedules (local occupancy/fan/
   lighting/AC timing is implemented; production policy and deployment remain
   future work).
6. Prediction recommendations and energy-savings analytics.
7. Dashboard UI, hardening, testing and deployment.

### Local MQTT ingestion demo

For ESP32 hardware control, first follow the broker credential, firmware, and
low-voltage safety setup in [firmware/esp32/README.md](firmware/esp32/README.md).
The local broker uses separate ESP32, ingestion, and dispatcher accounts; do
not start the broker without its ignored local password file. Keep
`LOCAL_HARDWARE_CONTROL_ENABLED=false` unless testing the localhost prototype
on an isolated, low-voltage load.

On a fresh database, Compose applies the schema. Apply both migrations to an
existing local database; the second adds raw LM393 ADC/PIR fields and the third
adds persisted occupancy automation timers and urgency state:

```powershell
docker compose up -d
Get-Content database\migrations\002_environment_raw_adc.sql |
  docker exec -i aceo-postgres psql -U aceo -d aceo
Get-Content database\migrations\003_occupancy_automation_state.sql |
  docker exec -i aceo-postgres psql -U aceo -d aceo
Set-Location backend
if (-not (Test-Path .env)) { Copy-Item ..\.env.example .env }
uv sync --locked --extra dev
uv run python scripts/seed_local_classroom.py
uv run python -m uvicorn app.main:app --reload --port 8000
```

For a synthetic-data test without the ESP32, publish generated readings from a
second terminal. Do not run this alongside the physical board, because the
latest synthetic reading can overwrite the dashboard's sensor values:

```powershell
Set-Location backend
python scripts/simulate_environment.py `
  --site-id c7ef6d75-7c12-461a-bb49-73f917c3357d `
  --classroom-id 26e97d46-7a34-4439-922a-83684bda557d
```

Start the frontend with `pnpm dev` from `frontend`. Dashboard sections still
use mock data except the temperature, humidity and light cards, which poll the
latest stored sensor reading. To enable physical ON/OFF controls on localhost
only, set `LOCAL_HARDWARE_CONTROL_ENABLED=true` in `backend/.env` and
`NEXT_PUBLIC_ENABLE_HARDWARE_CONTROLS=true` in `frontend/.env.local`. The
local flag applies only to loopback hostnames. Cloudflare control requires
its separate opt-in and authenticated private bridge. Set
`NEXT_PUBLIC_API_BASE_URL=http://localhost:8000` and
`NEXT_PUBLIC_CLASSROOM_ID=26e97d46-7a34-4439-922a-83684bda557d` in
`frontend/.env.local`:

```text
NEXT_PUBLIC_API_BASE_URL=http://localhost:8000
NEXT_PUBLIC_CLASSROOM_ID=26e97d46-7a34-4439-922a-83684bda557d
NEXT_PUBLIC_DASHBOARD_POLL_INTERVAL_MS=5000
NEXT_PUBLIC_ENABLE_HARDWARE_CONTROLS=true
NEXT_PUBLIC_USE_LIVE_OCCUPANCY=true
```

Use the firmware's Auto/Manual and digital ON/OFF controls only. The
fan-speed, brightness, AC setpoint, power, and energy panels are explicitly
simulated/unsupported. Keep FastAPI and the plaintext MQTT broker private;
the Cloudflare Worker exposes only its authenticated, allowlisted API routes.

Verify that the worker is connected, readings are stored and live telemetry responds:

```powershell
Invoke-WebRequest http://localhost:8000/health/ready
docker exec aceo-postgres psql -U aceo -d aceo -c `
  "SELECT sensor_id, temperature_c, received_at FROM environment_readings ORDER BY received_at DESC LIMIT 10;"
Invoke-WebRequest http://localhost:8000/api/v1/classrooms/26e97d46-7a34-4439-922a-83684bda557d/telemetry/latest
```
