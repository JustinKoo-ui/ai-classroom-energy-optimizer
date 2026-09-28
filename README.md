# AI Classroom Energy Optimizer

Project for monitoring classroom occupancy, comfort and
electrical energy, then issuing appliance commands through a deterministic
control policy.

This repository currently contains the initial architecture, protocol
contracts, database schema and shared types. It intentionally does not contain
a complete dashboard or device-control implementation yet.

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
|-- frontend/                React application (contracts only at this stage)
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

The repository can currently be deployed as a public **demo dashboard**. The
frontend uses `MockDashboardService` by default, so the deployed link will show
simulated classroom readings and will not control real appliances. This is the
recommended first deployment because the backend routes, database migrations
and MQTT dispatcher are not implemented yet.

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

When the FastAPI API is implemented and hosted over HTTPS, configure these
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
3. Manual control through the deterministic policy and acknowledgement loop.
4. Occupancy detection/tracking and privacy validation.
5. Comfort automation, vacancy handling and schedules.
6. Prediction recommendations and energy-savings analytics.
7. Dashboard UI, hardening, testing and deployment.

### Local MQTT ingestion demo

Start the local services, then start the FastAPI ingestion worker:

```powershell
docker compose up -d
Set-Location backend
python -m uvicorn app.main:app --reload --port 8000
```

In another terminal, publish valid environment telemetry continuously. Replace
the IDs if you are using different seeded site and classroom records:

```powershell
Set-Location backend
python scripts/simulate_environment.py `
  --site-id c7ef6d75-7c12-461a-bb49-73f917c3357d `
  --classroom-id 26e97d46-7a34-4439-922a-83684bda557d
```

Verify that the worker is connected and rows are being persisted:

```powershell
Invoke-WebRequest http://localhost:8000/health/ready
docker exec aceo-postgres psql -U aceo -d aceo -c `
  "SELECT sensor_id, temperature_c, received_at FROM environment_readings ORDER BY received_at DESC LIMIT 10;"
```
