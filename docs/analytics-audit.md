# Initial analytics audit

Inspected the existing checkout before implementation on 26 October 2026.
Existing uncommitted occupancy automation, hardware control, firmware and UI
changes are preserved.

- One repository contains FastAPI, PostgreSQL, a React/vinext frontend,
  MQTT ingestion/dispatcher, ESP32 firmware and a separate camera worker.
- `GET /api/v1/classrooms/{id}/devices` exposes firmware `reported_state`,
  freshness, faults and pending commands. Commands and desired state are not
  proof that a relay has changed. Firmware reports its software output state,
  not independently measured appliance operation.
- General dashboard, environment and occupancy polling defaults to 5 seconds.
  Local device/urgency polling is 2 seconds; command status polling is 500 ms.
  The backend ingests MQTT continuously without an open browser. Firmware
  publishes device snapshots approximately every 2 seconds.
- `device_states` holds only current state. `device_state_history` stores the
  first snapshot for each device/state version, suppressing later heartbeats.
  It loses retained-message provenance, so retained replays cannot be separated
  from live reports. `command_events` contains acknowledgements, but those
  isolated points do not establish continuous observation coverage.
- Existing history is therefore insufficient for trustworthy daily runtime.
  It will remain available in its original operational tables; no earlier
  continuous runtime will be invented from those sparse points.
- Environment and aggregate camera observations are already persisted. These
  can be reused for period summaries without sending images or raw messages
  to an external provider. There is no existing LLM integration.
- Existing dashboard power, energy, savings and AI insights are simulated.
  Meter-oriented energy tables exist in the schema, but there is no calibrated
  energy meter in this prototype. None of these values supplies the new model.
- Timestamps use UTC; classrooms have an IANA reporting timezone, locally
  `Asia/Kuala_Lumpur`. Existing local state freshness is 15 seconds.

## Implementation decision

Add a separate compressed runtime segment table at the existing live MQTT
state ingestion point, after the operational transaction. Ignore retained,
backlogged and out-of-order snapshots. Extend unchanged live segments instead
of adding duplicate transition events. Split segments after freshness gaps.
Tracking begins at the first reliable new observation. Analytics failures must
not prevent device-state persistence or command processing.

Use a bounded last-observation-carried-forward estimate: a snapshot supports
its reported state until the next snapshot or the freshness deadline,
whichever comes first. Beyond that deadline, state is UNKNOWN, never OFF.
Snapshot times are publication observations, not exact physical transitions.
Estimated transition counts are observed changes across contiguous coverage;
an initially ON device does not establish an observed activation.

Build and validate runtime first, deterministic estimated energy second, and
optional server-side LLM commentary last. The new module has no command or
automation mutation capability. Yellow light, blue fan and red AC indicators
apply only to the new analytics UI.
