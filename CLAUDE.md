# AI Classroom Energy Optimizer — Engineering Guide

## Mission

Build a privacy-preserving classroom energy optimizer that maintains comfort,
reduces avoidable electrical use and makes every physical action explainable.

## Non-negotiable invariant

AI, ML and computer-vision code **never controls an appliance directly**.

The only valid write path is:

```text
observation/prediction/recommendation
  -> deterministic control policy
  -> immutable allow decision
  -> persisted command/outbox
  -> MQTT dispatcher
  -> ESP32 acknowledgement
```

Do not add a shortcut, even for prototypes, demos, schedules or manual UI
controls. Broker ACLs must also make the shortcut impossible.

## Read before changing behavior

- `docs/architecture.md` — boundaries, data flow and deployment.
- `docs/control-policy.md` — normative automation and safety rules.
- `docs/requirements-traceability.md` — mapping from objectives to components.
- `docs/api/rest.md` — REST contract.
- `docs/api/websocket.md` — deferred real-time browser contract.
- `docs/mqtt.md` — device topics, payloads, QoS and ACLs.
- `database/schema.sql` — persistence and audit model.

## Repository boundaries

- `backend/app/domain`: pure entities, value objects, policies and ports. No
  FastAPI, database, MQTT or Roboflow imports.
- `backend/app/services`: use cases that coordinate domain and ports.
- `backend/app/repositories`: PostgreSQL implementations.
- `backend/app/integrations`: MQTT, Roboflow and external adapters.
- `backend/app/api`: transport only; thin route handlers.
- `cv_service`: frames, detections and process-local anonymous tracking.
- `ml`: reproducible feature/model training and inference.
- `frontend`: presentation and user interaction; never talks to MQTT.
- `firmware`: validates command envelopes and enforces local interlocks.

## Contract rules

1. External contracts are versioned. Current REST path and message schema are
   `v1`.
2. Wire fields use `snake_case` in REST and MQTT. TypeScript keeps
   these names to prevent hidden mapping bugs.
3. Identifiers are UUID strings. Timestamps are timezone-aware UTC ISO 8601.
4. Every MQTT message has `schema_version`, `message_id` and `occurred_at`.
5. Update Pydantic models, TypeScript interfaces and protocol documentation in
   the same change.
6. Additive optional fields are backward compatible. Renames, removals and
   semantic changes require a new schema/API version.

## Safety implementation rules

- Deny on missing, invalid, conflicting or stale safety-relevant state.
- Treat stale occupancy as `unknown`, never `vacant`.
- Keep desired and reported device state separate.
- Require command idempotency and reconcile acknowledgements.
- Use hysteresis, cooldowns and minimum on/off times to protect equipment.
- A manual override outranks automation but not hardware/electrical interlocks.
- Store the complete policy input snapshot and stable reason codes.
- Do not persist faces, frames, crops or biometric identifiers by default.

## Backend conventions

- Python 3.12+, FastAPI, Pydantic v2, SQLAlchemy 2 async and Alembic.
- Type hints are required. Use UTC-aware `datetime` values.
- Route handlers call use cases; business rules do not live in routers.
- Database mutations that create an allowed decision and command are atomic.
- Use structured logs and correlation IDs. Never log secrets or raw frames.
- Raise typed domain errors and map them to the documented error envelope.

## Frontend conventions

- React + TypeScript in strict mode.
- Server state remains separate from ephemeral UI state.
- REST creates/changes durable resources and supplies polled dashboard updates.
  WebSocket delivery is deferred.
- Manual controls show pending, acknowledged, applied, rejected and timed-out
  states. Never optimistically claim that a physical device changed.
- Provide keyboard access, readable chart labels and non-color-only statuses.

## Data and ML conventions

- Training data is time-split to avoid future leakage.
- Store model and feature-set versions with every prediction.
- Calibrate and report probability quality, not just classification accuracy.
- Define the 60-second departure label precisely and test class imbalance.
- Energy savings are estimates against a versioned baseline; display method and
  uncertainty and permit negative savings.

## Testing gates

Before merging control changes:

1. Run unit tests including policy allow/deny truth tables.
2. Run contract tests for REST and MQTT examples.
3. Run integration tests with PostgreSQL and a test MQTT broker.
4. Simulate stale sensors, CV loss, duplicate MQTT delivery, offline devices,
   conflicting override/schedule, command timeout and rapid state changes.
5. For firmware/high-power changes, pass hardware-in-the-loop testing on an
   isolated low-risk load before supervised mains testing.

## Scope of the current increment

Only architecture, folder structure, schema and contracts are established.
Do not build the complete UI or pretend placeholder control logic is safe for
real appliances. Implement future increments vertically with tests.
