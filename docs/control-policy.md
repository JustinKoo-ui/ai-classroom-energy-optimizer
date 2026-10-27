# Deterministic Safety and Control Policy

This document is normative. If implementation behavior conflicts with this
document, the safer behavior wins until the conflict is reviewed.

## Inputs

Each evaluation receives an immutable snapshot containing:

- classroom occupancy status, count, confidence and observation age;
- temperature, humidity and lux values with age and validity;
- per-device availability, capabilities, desired state and reported state;
- active manual override and schedule;
- recent command timestamps and failure state;
- optional AI recommendation with probability, model version and expiry;
- actor identity and role when the trigger is manual; and
- the active, versioned policy configuration.

## Universal hard checks

Before any device-specific rule, deny a command when:

1. the classroom or device identifier is unknown or disabled;
2. the actor is unauthorized for a manual action;
3. the device is offline, reports a fault or has an active safety interlock;
4. a required sensor/occupancy input is stale or invalid;
5. the action/value is outside the registered device capabilities;
6. the request or recommendation has expired;
7. the command cooldown or maximum switching-rate limit is violated;
8. an equal command is already queued, sent or applied;
9. the active override conflicts with an automatic request; or
10. the policy version cannot be loaded.

Every denial records stable machine-readable reason codes and a human-readable
summary. Unknown state does not default to permission.

## Air conditioner

- Occupied target: 26 °C with a configurable deadband (default ±0.5 °C).
- Only operate automatically with fresh confirmed occupancy and temperature.
- Enforce configured minimum/maximum setpoint, compressor minimum-on and
  minimum-off times, and a switching-rate limit.
- A leave-soon prediction may recommend delaying a new cooling cycle. It may not
  turn the AC off while occupancy is still confirmed.
- Automatic off requires confirmed continuous vacancy for the vacancy grace
  period. Loss of camera/sensor data is not vacancy.

## Fans

- Use deterministic temperature/humidity bands with hysteresis.
- Fan speed must be one of the device capability values.
- Prefer fan assistance near the upper comfort boundary before a more expensive
  cooling stage when policy configuration allows it.
- Do not oscillate speed more frequently than the configured cooldown.

## Lighting

- Operate automatically only with fresh occupancy and lux readings.
- Map lux deficit to an allowed brightness step; do not infer arbitrary PWM.
- Use hysteresis around the target lux to prevent flicker.
- Switch off only after confirmed continuous vacancy for the lighting vacancy
  grace period.

## Vacancy state machine

```text
OCCUPIED -- fresh count=0 --> POSSIBLY_VACANT
POSSIBLY_VACANT -- count>0 --> OCCUPIED
POSSIBLY_VACANT -- observation stale --> UNKNOWN
POSSIBLY_VACANT -- grace elapsed with fresh count=0 --> VACANT_CONFIRMED
VACANT_CONFIRMED -- count>0 --> OCCUPIED
any state -- critical input unavailable --> UNKNOWN
UNKNOWN -- fresh count>0 --> OCCUPIED
UNKNOWN -- fresh count=0 --> POSSIBLY_VACANT
```

Equipment shutoff is evaluated only after entering `VACANT_CONFIRMED`.

## Manual control and override

A one-time manual command still passes universal hard checks. An override
temporarily fixes or constrains a device state and suppresses lower-priority
automatic actions. Overrides require an owner, reason, start and expiry time;
indefinite overrides are disabled by default. Expiry returns the device to
automatic evaluation, not necessarily to its previous physical state.

### Local ESP32 relay prototype

The localhost-only prototype uses independently reported per-device `auto`
and `manual` modes. Manual mode remains active until the operator explicitly
switches that device to Auto. While manual, occupancy and sensor policy does
not change that relay. Urgency temporarily takes precedence without changing
the selected modes and restores prior Manual states when it ends.

For this prototype, a fresh camera count >= 1 immediately requests the fan
ON. Automatic lighting uses PIR as its first gate, independent of the camera
count: without fresh PIR motion, the light is OFF even at ADC 4095. Only with
fresh PIR motion does a fresh valid 12-bit LDR ADC value > 1000 request lighting
ON. ADC <= 1000 requests OFF even during motion. Both conditions must hold:
`fresh PIR motion AND fresh valid LDR ADC > 1000`. A stale or invalid input
cannot satisfy its gate, so automatic lighting is requested OFF.

A fresh camera count >= 3 starts the continuous AC qualification timer.
After at least 5 continuous seconds the policy requests AC ON, and only when
a fresh valid temperature is >=26°C OR a fresh valid relative humidity is
>=65%. Either independently valid reading can satisfy its threshold; invalid
readings never do. AC is requested OFF when occupancy falls below three,
occupancy becomes unknown/stale, environment data becomes stale, or neither
threshold can be established. There is no additional vacancy grace period.

Without confirmed occupants, a fresh PIR rising motion event starts a
non-blocking 20-second camera-scanning window. This applies
to both a valid zero count and unavailable camera data; these remain distinct
input states. Sustained HIGH does not extend or restart a scan. A fresh LOW
re-arms the next rising event. PIR alone never requests fan or AC ON.

Any fresh count >= 1 during the scan cancels its timeout and immediately
requests the fan ON. Throughout scanning, handoff, and normal operation, the
light follows the same PIR-then-LDR gates; the timer never forces it ON.
If no occupant is confirmed by timeout, the scan ends. Sustained PIR HIGH
cannot restart or extend the scan timer. When PIR becomes LOW or stale,
automatic lighting is requested OFF regardless of LDR or camera occupancy.
With no confirmed occupancy, the fan and AC remain OFF; only motion together
with ADC > 1000 can request lighting ON. Unknown/stale occupancy is never
reported as a valid zero and resets AC
qualification. Normal AC qualification also restarts after urgency control.
Qualification also resets after an unobserved backend evaluation gap longer
than the occupancy freshness limit, a gap between camera observations longer
than that limit, or a stored below-three observation within the qualifying
period, even if the latest count has already recovered.

These are targets evaluated by the existing one-second backend worker, not
claims of instantaneous physical switching. Per-device Manual priority,
device freshness, faults, pending commands, cooldowns and acknowledgements
continue to gate actual commands. The existing telemetry contract requires
both DHT22 values and skips a sample if either is invalid; this policy change
does not alter that acquisition or message format.

Urgency immediately requests all three relays on and monitors for a fixed
five minutes. At expiry, a fresh zero count turns all three off. A fresh
nonzero count resumes the selected per-device policy; unknown occupancy does
not prove absence and therefore does not trigger the no-presence shutoff.
All local prototype settings are implemented in the deterministic backend
policy and its persisted automation state; firmware only validates/applies
commands and reports actual output state.

## Schedules

Schedules create policy requests, not commands. At execution time, the scheduler
loads current state and the policy evaluates the request normally. Missed runs
outside their allowed lateness window are recorded but not replayed.

## AI use

AI recommendations are advisory and time-limited. Policy treats probability as
one input bounded by configured thresholds. AI output cannot:

- publish to MQTT command topics;
- create a `device_commands` row directly;
- bypass vacancy confirmation, sensor freshness or electrical limits;
- override a user; or
- modify policy configuration.

## Required decision record

Each evaluation persists trigger, actor, input snapshot, requested action,
matching rule, allow/deny result, reason codes, bounded output (if allowed),
policy version and timestamps. The record is immutable.
