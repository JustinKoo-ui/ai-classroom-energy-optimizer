# Frontend dashboard

Responsive React dashboard for live classroom occupancy, comfort, AI insight,
device control and energy analytics. REST loads dashboard data and creates
durable control requests. The dashboard polls for updates every five seconds
by default; the browser never connects to MQTT.

## Data-service boundary

`src/api/dashboard-service.ts` selects a `DashboardService` implementation:

- `MockDashboardService` is enabled by default. It simulates changing readings
  and asynchronous policy-approved commands.
- `FastApiDashboardService` contains the REST transport for later integration.
  Set `NEXT_PUBLIC_USE_MOCK_DATA=false`, API base URL and classroom ID when the
  matching backend aggregate adapter is available.

Set `NEXT_PUBLIC_DASHBOARD_POLL_INTERVAL_MS` to change the polling interval
(milliseconds; minimum 1000). WebSocket support is deferred.

UI components consume typed dashboard state and contain no embedded sensor or
energy values. The demo-state selector exercises healthy operation, sensor
failure, device disconnection and feed loss.

## Key source locations

- `src/features/dashboard/Dashboard.tsx` — page composition.
- `src/components/` — reusable metrics, controls, AI and chart components.
- `src/hooks/useDashboard.ts` — polling, loading and command state.
- `src/types/contracts.ts` — platform wire contracts.
- `src/types/dashboard.ts` — dashboard view model/service contract.
