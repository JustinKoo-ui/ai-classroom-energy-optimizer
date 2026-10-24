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
endpoint is currently loopback-only, so it is not available from a public
website deployment.

## Data-service boundary

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
fresh online device state. The Cloudflare-hosted website always remains a
demo and cannot control the local ESP32.

The current board supports digital ON/OFF and Auto/Manual only. Fan speed,
brightness, AC setpoint, wattage, and energy remain disabled/simulated and are
never sent as device commands. See the ESP32 setup and safety limits in
[`../firmware/esp32/README.md`](../firmware/esp32/README.md).

UI components consume typed dashboard state and contain no embedded sensor or
energy values. The demo-state selector exercises demo-panel operation, device
disconnection and demo feed loss.

## Key source locations

- `src/features/dashboard/Dashboard.tsx` — page composition.
- `src/components/` — reusable metrics, controls, AI and chart components.
- `src/hooks/useDashboard.ts` — polling, loading and command state.
- `src/types/contracts.ts` — platform wire contracts.
- `src/types/dashboard.ts` — dashboard view model/service contract.
