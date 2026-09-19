# AI Classroom Energy Optimizer

Safety-first final-year project for monitoring classroom occupancy, comfort and
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
|   |-- app/api/v1/          Versioned REST and WebSocket routes
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

### Option A: deploy with Vercel (recommended)

1. Push this repository to GitHub.
2. In Vercel, select **Add New Project**, import the repository, and set
   **Root Directory** to `frontend`.
3. Use these project settings:

   ```text
   Install Command: pnpm install --frozen-lockfile
   Build Command:   pnpm run build
   Output Directory: leave the framework default
   ```

4. Add these environment variables for the **Production**, **Preview** and
   **Development** environments:

   ```text
   NEXT_PUBLIC_USE_MOCK_DATA=true
   NEXT_PUBLIC_CLASSROOM_ID=26e97d46-7a34-4439-922a-83684bda557d
   ```

5. Click **Deploy**. Vercel will provide an HTTPS URL such as
   `https://your-project.vercel.app`. Every push to the selected Git branch
   will create a new deployment.

The same deployment can be created from a terminal after installing the Vercel
CLI:

```powershell
Set-Location frontend
corepack enable
pnpm install --frozen-lockfile
pnpm run build
npx vercel
```

Do not put database passwords, JWT signing keys, MQTT credentials, Roboflow
keys or private certificates in `NEXT_PUBLIC_*` variables. Variables with that
prefix are bundled into browser JavaScript and are public.

### Option B: connect a deployed backend later

When the FastAPI API and WebSocket routes are implemented and hosted over HTTPS,
change the Vercel variables to:

```text
NEXT_PUBLIC_USE_MOCK_DATA=false
NEXT_PUBLIC_API_BASE_URL=https://api.example.com
NEXT_PUBLIC_CLASSROOM_ID=26e97d46-7a34-4439-922a-83684bda557d
```

The API must allow the deployed frontend origin in
`CORS_ALLOWED_ORIGINS`, support credentials if authentication is enabled, and
serve WebSockets over `wss://`. Deploy PostgreSQL, the MQTT broker, the
dispatcher and the FastAPI service separately; the browser must never connect
directly to MQTT. Use the deployment variables in [.env.example](.env.example)
as a server-side configuration checklist, but do not commit a populated `.env`
file.

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
2. Telemetry ingestion, current room state and real-time dashboard transport.
3. Manual control through the deterministic policy and acknowledgement loop.
4. Occupancy detection/tracking and privacy validation.
5. Comfort automation, vacancy handling and schedules.
6. Prediction recommendations and energy-savings analytics.
7. Dashboard UI, hardening, testing and deployment.
