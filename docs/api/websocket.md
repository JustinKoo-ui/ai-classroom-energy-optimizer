# WebSocket API v1

Endpoint: `/api/v1/ws/classrooms/{classroom_id}`

Use a short-lived access token in the initial authorized handshake (preferred:
secure cookie or negotiated subprotocol; query token only in local development).
The server checks classroom access before accepting the connection.

REST is authoritative for mutations and historical queries. WebSocket provides
low-latency state changes; it is not a durable command channel.

## Server envelope

```json
{
  "schema_version": "1.0",
  "event_id": "uuid",
  "type": "occupancy.updated",
  "occurred_at": "2026-08-14T03:30:00Z",
  "classroom_id": "uuid",
  "sequence": 1042,
  "data": {}
}
```

`sequence` is monotonic per classroom within a backend event-stream epoch. A
client that sees a gap or reconnects fetches `/classrooms/{id}/state`; delivery
is at-least-once, so clients deduplicate `event_id`.

## Server message types

| Type | Data | When emitted |
|---|---|---|
| `connection.ready` | `connection_id`, `heartbeat_seconds` | Handshake succeeds |
| `snapshot.updated` | Complete `ClassroomSnapshot` | Subscribe or resync |
| `occupancy.updated` | `OccupancyState` | Count/status/freshness changes |
| `environment.updated` | `EnvironmentState` | Temperature, humidity or lux changes |
| `device.state_updated` | `DeviceState` | Availability/reported/desired state changes |
| `energy.updated` | `EnergyUpdate` | Current power/period energy update |
| `prediction.updated` | `DeparturePrediction` | New or expired model result |
| `recommendation.created` | `AIRecommendation` | Advisory record created |
| `control.decision` | `ControlDecisionSummary` | Policy allows or denies a request |
| `command.status` | `DeviceCommand` | Queued/sent/ack/applied/failure transition |
| `override.updated` | `ManualOverride` | Override created/expired/cancelled |
| `schedule.updated` | `Schedule` | Schedule changes or run status |
| `alert.created` | `SystemEvent` | Fault, stale input or abnormal condition |
| `heartbeat` | `server_time` | Connection keepalive |
| `error` | `code`, `message`, optional `request_id` | Client message cannot be handled |

Example command transition:

```json
{
  "schema_version": "1.0",
  "event_id": "8d838f63-feb5-4e50-8f7a-587a71a34f36",
  "type": "command.status",
  "occurred_at": "2026-08-14T03:30:04Z",
  "classroom_id": "26e97d46-7a34-4439-922a-83684bda557d",
  "sequence": 1043,
  "data": {
    "id": "0b694edd-61a4-4d30-93cf-e5dd551cfd42",
    "device_id": "ace7d91a-f09f-4124-9864-997823ecda17",
    "decision_id": "a5ca49c2-62da-44d8-a780-a9247ec7ab85",
    "action": "set_power",
    "value": {"on": true},
    "status": "applied",
    "created_at": "2026-08-14T03:30:02Z",
    "updated_at": "2026-08-14T03:30:04Z"
  }
}
```

## Client envelope

```json
{
  "schema_version": "1.0",
  "request_id": "uuid",
  "type": "snapshot.request",
  "data": {}
}
```

Allowed client messages:

| Type | Data | Purpose |
|---|---|---|
| `snapshot.request` | empty | Request current snapshot after a gap |
| `stream.configure` | `streams[]` | Select optional high-frequency streams |
| `ping` | optional `client_time` | Connectivity check |

Device commands, overrides and schedule mutations are deliberately not accepted
over WebSocket; use REST so authorization, idempotency and audit behavior remain
consistent.

## Connection behavior

- Server sends `connection.ready`, then `snapshot.updated`.
- Default coalescing limits environment/energy updates to one per second per
  classroom; safety processing is not delayed by UI coalescing.
- Client replies to heartbeat with `ping` or sends application traffic within
  the configured timeout.
- Slow clients are disconnected with code `4008` and resynchronize via REST.
- Authentication expiry closes with `4001`; authorization failure uses `4003`;
  incompatible schema uses `4006`; maintenance uses `1012`.
- Clients retry with exponential backoff and jitter, capped at 30 seconds.

