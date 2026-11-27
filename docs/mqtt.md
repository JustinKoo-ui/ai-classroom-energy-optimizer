# MQTT Contracts

Topic root: `aceo/v1/{site_id}/{classroom_id}`

MQTT is the device boundary. It is not exposed to the React frontend. Production
connections use TLS and unique credentials per device, gateway, CV worker and
backend dispatcher.

Backend ingestion and command dispatch start their network loops even when
the broker is initially unavailable. They retry connections with a 1–30 second
backoff, and ingestion resubscribes after each successful connection. The API
reports degraded readiness while disconnected. Reconnection does not refresh
old observations: their original `occurred_at` still determines freshness.
For local ESP32 connection and changing LAN-address checks, see the
[firmware troubleshooting guide](../firmware/esp32/README.md#readings-repeatedly-become-stale).

## Topics

| Topic suffix | Publisher | Subscriber | QoS | Retain |
|---|---|---|---:|---:|
| `telemetry/environment` | sensor gateway | backend | 1 | no |
| `telemetry/power/{device_id}` | energy meter | backend | 1 | no |
| `occupancy/observation` | authorized CV worker | backend | 1 | no |
| `device/{device_id}/availability` | device LWT/device | backend | 1 | yes |
| `device/{device_id}/state` | device | backend | 1 | yes |
| `device/{device_id}/event` | device | backend | 1 | no |
| `command/{device_id}/set` | backend dispatcher only | named device | 1 | no |
| `command/{device_id}/ack` | named device | backend | 1 | no |
| `gateway/{gateway_id}/status` | gateway | backend | 1 | yes |
| `solar/{producer_id}/telemetry` | solar tracker | backend | 1 | no |
| `solar/{producer_id}/status` | solar tracker or its LWT | backend | 1 | yes |

Commands and acknowledgements must never be retained. QoS 1 means duplicates
are valid; consumers deduplicate `message_id`, and firmware deduplicates
`command_id`/`idempotency_key` across reconnects.

## Common envelope

Every payload is UTF-8 JSON:

```json
{
  "schema_version": "1.0",
  "message_id": "uuid",
  "occurred_at": "2026-08-14T03:30:00Z",
  "site_id": "uuid",
  "classroom_id": "uuid",
  "producer_id": "esp32-env-01",
  "payload": {}
}
```

Consumers reject unsupported major versions, malformed UUID/timestamps, topic
and payload identity mismatches, timestamps too far in the future and payloads
outside physical bounds. Rejected payloads cannot update authoritative state.
Solar, classroom schema `3.0`, and gateway status messages can carry a null
`occurred_at` when device wall time is unavailable. A gateway Last Will
also uses a null timestamp because the broker publishes it on disconnection.
Schema `1.0` remains supported for calibrated lux senders. New LM393 sensor
gateways use environment payload schema `2.0` and report raw ADC counts instead
of claiming an uncalibrated lux value.

## Environment telemetry

Topic: `.../telemetry/environment`

```json
{
  "schema_version": "1.0",
  "message_id": "22ee6fe6-e6f0-474e-bcdd-b708b0d73b48",
  "occurred_at": "2026-08-14T03:30:00Z",
  "site_id": "c7ef6d75-7c12-461a-bb49-73f917c3357d",
  "classroom_id": "26e97d46-7a34-4439-922a-83684bda557d",
  "producer_id": "esp32-env-01",
  "payload": {
    "sensor_id": "env-01",
    "temperature_c": 26.4,
    "relative_humidity_pct": 66.0,
    "illuminance_lux": 240.0,
    "sample_interval_ms": 5000
  }
}
```

### Environment telemetry schema 2.0

Topic: `.../telemetry/environment`

```json
{
  "schema_version": "2.0",
  "message_id": "22ee6fe6-e6f0-474e-bcdd-b708b0d73b48",
  "occurred_at": "2026-10-03T13:00:00Z",
  "site_id": "c7ef6d75-7c12-461a-bb49-73f917c3357d",
  "classroom_id": "26e97d46-7a34-4439-922a-83684bda557d",
  "producer_id": "esp32-room-a204",
  "payload": {
    "sensor_id": "esp32-room-a204",
    "temperature_c": 26.4,
    "relative_humidity_pct": 66.0,
    "illuminance_adc": 2048,
    "motion_detected": null,
    "sample_interval_ms": 5000
  }
}
```

`illuminance_adc` is the ESP32's 12-bit ADC count (0–4095), not lux. A PIR
signal describes motion detection only; it is not an occupant count or proof
that the room is vacant. It is `null` during the PIR module's 30-second startup
period. Both v1 and v2 observations are persisted in
`environment_readings`; lux is nullable for v2 readings.

### Classroom environment telemetry schema 3.0

The classroom ESP32 publishes one non-retained QoS 1 snapshot to
`.../telemetry/environment`. It uses its existing broker identity and ACL.

```json
{
  "schema_version": "3.0",
  "message_id": "398060ac-3df1-4bea-a9db-6c06216d110a",
  "occurred_at": "2026-11-26T04:00:00Z",
  "site_id": "c7ef6d75-7c12-461a-bb49-73f917c3357d",
  "classroom_id": "26e97d46-7a34-4439-922a-83684bda557d",
  "producer_id": "esp32-room-a204",
  "payload": {
    "sensor_id": "esp32-room-a204",
    "boot_id": "ac2d2196-95aa-4651-9780-84aca75b4d6e",
    "uptime_ms": 120000,
    "sample_interval_ms": 2000,
    "temperature_c": 27.5,
    "relative_humidity_pct": 68.0,
    "illuminance_lux": 250.0,
    "motion_detected": true,
    "voltage_v": 12.1,
    "current_a": 0.85,
    "active_power_w": 10.285,
    "energy_wh_total": 125.5,
    "energy_kwh_total": 0.1255,
    "relays": {"light": true, "fan": true, "air_conditioner": true},
    "sensors": {
      "dht22_available": true,
      "pir_ready": true,
      "bh1750_available": true,
      "bh1750_fresh": true,
      "bh1750_age_ms": 0,
      "ina219_available": true
    },
    "energy_storage_available": true
  }
}
```

`occurred_at` is null until the ESP32 can supply a network-synchronized UTC
timestamp. FastAPI retains its receipt time. Missing measurements are null.
An older lux value can be present with `bh1750_fresh: false` and its age.
Motion is null during PIR warm-up. The INA219 supplies signed current and
`active_power_w` (voltage × current) for its measured DC circuit. Positive
consumption accumulates from valid samples on the ESP32. The cumulative meter
can remain available during INA219 failures. The relay booleans represent
output commands.

FastAPI stores one validated row per `message_id` and delivers the snapshot
and its measured DC history through the
[prototype monitor](api/rest.md#classroom-prototype-monitor).

## Solar tracker telemetry

Topic `.../solar/solar-tracker-esp32/telemetry`. The example uses a manual test
sample with GPS disabled. Each actual message uses a new `message_id`.

```json
{
  "schema_version": "1.0",
  "message_id": "22ee6fe6-e6f0-474e-bcdd-b708b0d73b48",
  "occurred_at": null,
  "site_id": "c7ef6d75-7c12-461a-bb49-73f917c3357d",
  "classroom_id": "26e97d46-7a34-4439-922a-83684bda557d",
  "producer_id": "solar-tracker-esp32",
  "payload": {
    "boot_id": "45bf0c0d-0af4-47cb-be51-f6954556e8f6",
    "gps": {
      "enabled": false,
      "fix": false,
      "time_valid": false,
      "latitude": null,
      "longitude": null,
      "last_fix_age_ms": null
    },
    "tracking": {
      "mode": "manual",
      "target_azimuth": 120.5,
      "actual_azimuth": 119.8,
      "target_zenith": 40.2,
      "actual_zenith": 39.9,
      "azimuth_pwm": 0,
      "zenith_pwm": 0,
      "stop_reason": null
    },
    "solar_power": {
      "voltage": 12.5,
      "current": 1.25,
      "power": 15.625,
      "energy": 5.5
    },
    "sensors": {
      "ina219_available": true,
      "azimuth_mpu_available": true,
      "zenith_mpu_available": true
    }
  }
}
```

Angles and coordinates are in degrees. PWM values describe commanded 8-bit
duty. Global stop reasons are `waiting_for_gps`, `waiting_for_clock`, `spa_error`,
`feedback_unavailable`, `zenith_limit`, and `local_hold`. Electrical units are V, A, W, and
Wh. A null number means there was no valid reading for that field. When `gps.enabled`
is false, the fix and time flags are false and the remaining GPS fields are null.
Older messages without `enabled` are treated as GPS enabled. The Wh
counter is local to the boot or the last `RESET_WH` command. The backend stores
each accepted sample once by message ID and records its own UTC receipt time.

`tracking.yaw_sequence` is optional. When a Serial `TEST` or `RUN` has started,
it reports `kind`, `phase`, `leg_progress_deg`, `leg_target_deg`,
`net_rotation_deg`, and `result`. The phase is `clockwise`, `pause`,
`anticlockwise`, or `idle`. The result is null during motion and describes the
completion or stop afterward. The sequence reports relative travel with
clockwise positive. It has no absolute tracking target or browser command.

`tracking.fixed_yaw` is optional and appears in fixed-tilt mode. It reports
`clock_set`, `referenced`, `calibrating`, `armed`, `reachable`, `status`,
`demo_speed`, and `solar_time`. `solar_time` is the timezone-aware time used to
calculate the sun’s position and can be accelerated in a Serial demo. MQTT
`occurred_at` and the backend receipt time continue to describe actual sample
timing. A fixed-tilt sample can report a calculated target zenith and an
available tilt measurement while `zenith_pwm` remains zero.

Topic `.../solar/solar-tracker-esp32/status` uses the same envelope with a payload
of `{"boot_id":"45bf0c0d-0af4-47cb-be51-f6954556e8f6","status":"online"}`.
A Last Will uses status `offline` and a null `occurred_at`, since the Will is
prepared at connect time. Availability and sample freshness are tracked separately.
The solar account has no relay command permissions.

## Power telemetry

Topic: `.../telemetry/power/{device_id}`

```json
{
  "schema_version": "1.0",
  "message_id": "3a560974-fda2-41c1-89db-e84517cdba16",
  "occurred_at": "2026-08-14T03:30:00Z",
  "site_id": "c7ef6d75-7c12-461a-bb49-73f917c3357d",
  "classroom_id": "26e97d46-7a34-4439-922a-83684bda557d",
  "producer_id": "esp32-power-01",
  "payload": {
    "device_id": "ace7d91a-f09f-4124-9864-997823ecda17",
    "voltage_v": 239.2,
    "current_a": 3.41,
    "active_power_w": 781.5,
    "apparent_power_va": 815.7,
    "power_factor": 0.958,
    "energy_wh_total": 182349.2,
    "sample_interval_ms": 5000
  }
}
```

The backend derives interval energy from a monotonic cumulative meter when
available and detects resets; it does not silently treat a reset as negative
consumption.

## Occupancy observation

Topic: `.../occupancy/observation`

```json
{
  "schema_version": "1.0",
  "message_id": "db12eb56-9743-4f2f-880e-7303a54069b0",
  "occurred_at": "2026-08-14T03:30:00Z",
  "site_id": "c7ef6d75-7c12-461a-bb49-73f917c3357d",
  "classroom_id": "26e97d46-7a34-4439-922a-83684bda557d",
  "producer_id": "cv-worker-01",
  "payload": {
    "camera_id": "camera-01",
    "count": 12,
    "confidence": 0.94,
    "entries_delta": 1,
    "exits_delta": 0,
    "frame_sequence": 84591,
    "source": "roboflow_bytetrack"
  }
}
```

No frame, crop, face embedding or persistent person identifier is permitted in
this contract.

## Availability and reported state

Availability payload:

```json
{
  "status": "online",
  "boot_id": "a8c5bde0-5af4-4015-89a1-b85706dded53",
  "firmware_version": "1.0.0"
}
```

The broker last-will payload uses `status: offline`. Device state payload:

```json
{
  "device_id": "ace7d91a-f09f-4124-9864-997823ecda17",
  "state": {"on": true, "setpoint_c": 26.0},
  "fault_codes": [],
  "state_version": 381
}
```

These payloads are placed inside the common envelope. Retained state is treated
as a startup hint and its `occurred_at` still determines freshness. The local
ESP32 prototype reports controller availability on
`gateway/{producer_id}/status`; its relay state is reported on the per-device
state topics above.

## Approved command

Topic: `.../command/{device_id}/set`

```json
{
  "schema_version": "1.0",
  "message_id": "666e731a-3a1a-43fa-a9a9-bb1467a6f0d5",
  "occurred_at": "2026-08-14T03:30:02Z",
  "site_id": "c7ef6d75-7c12-461a-bb49-73f917c3357d",
  "classroom_id": "26e97d46-7a34-4439-922a-83684bda557d",
  "producer_id": "command-dispatcher-01",
  "payload": {
    "command_id": "0b694edd-61a4-4d30-93cf-e5dd551cfd42",
    "decision_id": "a5ca49c2-62da-44d8-a780-a9247ec7ab85",
    "device_id": "ace7d91a-f09f-4124-9864-997823ecda17",
    "action": "set_power",
    "value": {"on": true, "mode": "manual", "source": "manual"},
    "expires_at": "2026-08-14T03:30:32Z",
    "idempotency_key": "manual-8cb4b99e"
  }
}
```

Firmware checks device identity, expiry, supported action/value, control
source, mode priority and duplicate command ID before actuating. Automation
commands carry `mode: "auto"` and a source such as `automation`, `vacancy`, or
`pir_fallback`; urgency commands use `urgency`, `urgency_timeout`, or
`manual_restore`. These values remain inside the existing versioned command
envelope and topic structure.

## Command acknowledgement

Topic: `.../command/{device_id}/ack`

```json
{
  "schema_version": "1.0",
  "message_id": "8e3cb281-21b0-446f-8ad3-c4a17857478b",
  "occurred_at": "2026-08-14T03:30:04Z",
  "site_id": "c7ef6d75-7c12-461a-bb49-73f917c3357d",
  "classroom_id": "26e97d46-7a34-4439-922a-83684bda557d",
  "producer_id": "esp32-ac-01",
  "payload": {
    "command_id": "0b694edd-61a4-4d30-93cf-e5dd551cfd42",
    "device_id": "ace7d91a-f09f-4124-9864-997823ecda17",
    "status": "applied",
    "reason_code": null,
    "reported_state": {"on": true, "setpoint_c": 26.0},
    "applied_at": "2026-08-14T03:30:04Z"
  }
}
```

Status is `accepted`, `applied`, `rejected` or `failed`. An `accepted`
acknowledgement does not imply physical application; an `applied`
acknowledgement includes the resulting state.

## Broker ACL policy

- Sensor/device identity: publish only its exact telemetry, state, event,
  availability and acknowledgement topics; subscribe only to its exact command
  topic.
- CV identity: publish only `occupancy/observation` for assigned classrooms; no
  command subscription or publication.
- Prediction/AI identity: no device command permission. Prefer no direct broker
  access.
- Backend ingestion identity: subscribe to telemetry/state/event/ack topics; no
  command publication.
- Backend dispatcher identity: publish only `command/+/set`; read approved
  commands from PostgreSQL, not from AI output.
- Dashboard/browser: no broker identity.

Wildcard permissions must remain scoped below assigned site/classroom roots.

The checked-in local Mosquitto ACL uses separate password-authenticated
identities for the ESP32, backend ingestion, and backend dispatcher. The
ESP32 may publish only the local environment telemetry, its three relay state
topics, acknowledgements, and gateway status; it may subscribe only to the
three exact relay command topics. The ingestion identity is read-only, and
only the dispatcher identity may publish commands. Commands are always sent
with `retain=false`. The password file is local and ignored by Git.

The local broker is plaintext and scoped to the seeded development classroom.
Do not expose port 1883 publicly or use these credentials/ACLs in production.
Production requires TLS, unique per-device credentials, assigned-root ACLs,
and authenticated REST users.

## Local ESP32 relay prototype

The supplied ESP32 sketch is represented by one gateway controlling three
logical digital outputs. Each reported state contains `on` and `mode`
(`auto` or `manual`); state messages are retained, while commands and
acknowledgements are not. Output polarity is configured per device in
`firmware/esp32/include/relay_config.h`: yellow lighting, fan, and AC are active
HIGH (logical OFF drives LOW; logical ON drives HIGH). The `on` field
retains its logical meaning; it is not a GPIO level or physical light feedback.
Sensor acquisition runs every two seconds. Periodic telemetry/state snapshots
wait for previous publications to receive broker acknowledgements, preventing
an unbounded queue of old samples. Intermediate periodic snapshots may be
skipped; command acknowledgements and immediate state reports are not gated.
Broker acknowledgement is transport confirmation, not evidence that a device
command was applied. Freshness still uses the original observation timestamp.
Current firmware evaluates the classroom PIR, lux, temperature and humidity
rules locally in Auto. Its per-device reported state includes
`automation_owner: "firmware"`. The backend defers ordinary Auto commands
for those devices. Existing dispatcher commands still carry approved Manual,
mode and Urgency decisions. Gateways without the marker use the legacy
backend automation policy.

This firmware supports only `set_power` and `set_mode`; it rejects
`set_temperature`, `set_fan_speed`, and `set_brightness`. For this local relay
adapter, a manual `set_power` value includes `{"on": true, "mode":
"manual", "source": "manual"}`. An automatic value uses `mode: "auto"` and a
policy source. Per-device Manual mode is preserved across urgency operation.
For firmware-owned relays an approved Urgency command can include
`value.urgency_until` with a UTC deadline formatted as `YYYY-MM-DDTHH:MM:SSZ`.
The frontend uses backend REST and server-sent events. See
[`firmware/esp32/README.md`](../firmware/esp32/README.md) for the local setup
and low-voltage limitations.
