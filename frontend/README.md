# Frontend dashboard

Responsive React dashboard for live classroom occupancy, comfort, AI insight,
device control and energy analytics. REST loads dashboard data and creates
durable control requests. The dashboard polls for updates every five seconds
by default; the browser never connects to MQTT.

The temperature, humidity and ambient-light cards poll the backend's latest
sensor endpoint. Other dashboard sections still use `MockDashboardService`
until their live API contracts and data are implemented. The LM393 light value
is displayed as raw ADC counts, not lux; PIR data is stored as motion detection
and is not used as an occupant count.

Camera occupancy can be enabled independently without switching the other
dashboard panels off demo data. Configure
`NEXT_PUBLIC_USE_LIVE_OCCUPANCY=true`,
`NEXT_PUBLIC_API_BASE_URL=http://localhost:8000`, and the matching
`NEXT_PUBLIC_CLASSROOM_ID` in the frontend environment, then restart the
frontend. The backend must be running with its database and MQTT ingestion
connected, and the CV worker must be publishing observations to that broker.
The browser polls `GET /api/v1/classrooms/{classroom_id}/occupancy/current`
using the shared dashboard interval. Counts are shown only for fresh readings;
stale and missing observations display as unknown rather than zero. This
endpoint accepts direct loopback access or the authenticated private Worker
bridge described in [the Cloudflare guide](../docs/cloudflare-live.md).

## Data-service boundary

The AI insights card shares the overview's live occupancy request and five-second
polling interval. It shows the actual count, observation age/freshness, and a
bounded 60-second observed trend collected during the page session. This is
observation, not a departure forecast. The card expires counts after 15 seconds,
including during failed or stalled requests, and shows unknown rather than
assuming vacancy. Trend history resets across observation gaps or connection/
classroom changes. Reloading begins a new trend history.

No trained departure model is connected. The CV publisher currently sends a
fixed confidence of 0.97, so this card does not present it as measured accuracy
or prediction confidence. Demo departure probabilities, recommendations and
model versions are not used by this card. Energy charts keep their existing
demo data; this change does not call the AI provider or issue device commands.
The existing authenticated Cloudflare occupancy proxy also supports this card.
Run `pnpm test:insights` for freshness and observed-trend regression checks.

`src/api/dashboard-service.ts` selects a `DashboardService` implementation:

- `MockDashboardService` is enabled by default. It simulates changing readings
  and asynchronous demo interactions for panels that do not yet have live
  backend contracts. These simulated interactions are not sent to the ESP32.
  Temperature, humidity and ambient light are replaced by the live sensor
  endpoint when the local backend is configured.
- `FastApiDashboardService` contains the separate, not-yet-implemented full
  dashboard REST transport. Do not set `NEXT_PUBLIC_USE_MOCK_DATA=false` until
  that aggregate dashboard endpoint and the command API are implemented.

Set `NEXT_PUBLIC_DASHBOARD_POLL_INTERVAL_MS` to change the polling interval
(milliseconds; minimum 1000). WebSocket support is deferred.

## Local relay controls

The separate local hardware-control service is enabled only when
`NEXT_PUBLIC_ENABLE_HARDWARE_CONTROLS=true` and the page hostname is localhost.
It polls the loopback REST API and sends `set_power`/`set_mode` requests there;
the browser never connects to MQTT. The backend requires
`LOCAL_HARDWARE_CONTROL_ENABLED=true`, an allowed deterministic decision, and a
fresh online device state. Cloudflare relay controls have a separate opt-in:
`NEXT_PUBLIC_ENABLE_REMOTE_HARDWARE_CONTROLS=true`, an empty production API
base and an exact `NEXT_PUBLIC_HARDWARE_CONTROL_ORIGIN`. The Worker verifies
Cloudflare Access JWTs and checks a private operator email allowlist. The
backend independently checks the bridge secret, operator and relay scope.
See [the setup guide](../docs/cloudflare-live.md).

The current board supports digital ON/OFF and Auto/Manual only. Fan speed,
brightness, AC setpoint, wattage, and energy remain disabled/simulated and are
never sent as device commands. See the ESP32 setup and safety limits in
[`../firmware/esp32/README.md`](../firmware/esp32/README.md).

When hardware controls are enabled, the dashboard also polls urgency status.
Only localhost offers an urgency request through
`POST /api/v1/classrooms/{classroom_id}/urgency`. The status is policy
monitoring only; device cards continue to show the ESP32-reported output state.
The overview also reports fresh PIR motion as a sensor reading, not occupancy.

UI components consume typed dashboard state and contain no embedded sensor or
energy values. The demo-state selector exercises demo-panel operation, device
disconnection and demo feed loss.

## Recorded energy and AI commentary

The Environmental Analyst displays runtime coverage beside each recorded-energy
estimate. Combined coverage weights devices by their elapsed observation time.
Provisional and final comparison percentages are withheld until the reporting
period has ended with complete runtime coverage. Specific messages distinguish
an unfinished period, late tracking, missing observations and a zero baseline;
waiting until midnight cannot fill existing gaps.

Detailed calculation diagnostics, disclosures and generated data-quality notes
are available in collapsed **Methodology and limitations** panels. Numerical
analytics remain independent of AI commentary and hardware control.

Run the coverage/presentation regression tests without adding dependencies:

```powershell
pnpm test:analytics
pnpm test:proxy
pnpm test:hardware
```

## Key source locations

- `src/features/dashboard/Dashboard.tsx` — page composition.
- `src/components/` — reusable metrics, controls, AI and chart components.
- `src/hooks/useDashboard.ts` — polling, loading and command state.
- `src/types/contracts.ts` — platform wire contracts.
- `src/types/dashboard.ts` — dashboard view model/service contract.
