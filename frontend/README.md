# Frontend dashboard

Responsive React dashboard for live classroom occupancy, comfort, AI insight,
device control and energy analytics. REST creates durable control requests;
WebSocket events update the interface. The browser never connects to MQTT.

## Data-service boundary

`src/api/dashboard-service.ts` selects a `DashboardService` implementation:

- `MockDashboardService` is enabled by default. It simulates WebSocket snapshot
  and device-state events and asynchronous policy-approved commands.
- `FastApiDashboardService` contains the REST/WebSocket transport boundary for
  later integration. Set `NEXT_PUBLIC_USE_MOCK_DATA=false`, API base URL and
  classroom ID when the matching backend aggregate adapter is available.

UI components consume typed dashboard state and contain no embedded sensor or
energy values. The demo-state selector exercises healthy operation, sensor
failure, device disconnection and realtime connection loss.

## Key source locations

- `src/features/dashboard/Dashboard.tsx` — page composition.
- `src/components/` — reusable metrics, controls, AI and chart components.
- `src/hooks/useDashboard.ts` — loading, subscription and command state.
- `src/types/contracts.ts` — platform wire contracts.
- `src/types/dashboard.ts` — dashboard view model/service contract.
