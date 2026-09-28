import type { ISODateTime, UUID } from "./contracts";

export type DataStatus = "ok" | "loading" | "stale" | "error";
export type ConnectionStatus = "connecting" | "connected" | "disconnected";
export type DeviceKind = "air_conditioner" | "fan" | "lighting";
export type DeviceMode = "auto" | "manual";
export type MockScenario = "live" | "sensor_error" | "device_offline" | "connection_loss";

export interface DashboardMetric {
  id:
    | "occupancy"
    | "temperature"
    | "humidity"
    | "ambient_light"
    | "current_power"
    | "energy_today"
    | "energy_saved";
  label: string;
  value: number | null;
  display_value: string;
  unit: string;
  detail: string;
  trend: "up" | "down" | "steady";
  trend_label: string;
  status: DataStatus;
  updated_at: ISODateTime;
}

export interface DashboardDevice {
  id: UUID;
  name: string;
  location: string;
  kind: DeviceKind;
  is_on: boolean;
  mode: DeviceMode;
  connected: boolean;
  pending: boolean;
  target_temperature_c: number | null;
  fan_speed: 0 | 1 | 2 | 3 | null;
  brightness_pct: number | null;
  active_power_w: number;
  status_label: string;
  updated_at: ISODateTime;
}

export interface AIInsight {
  occupants_detected: number;
  departure_probability: number;
  predicted_occupancy_trend: "rising" | "stable" | "falling";
  ai_control_state: "observing" | "recommending" | "paused";
  prediction_confidence: number;
  recommendation: string;
  model_version: string;
  expires_at: ISODateTime;
}

export interface TimeSeriesPoint {
  label: string;
  value: number;
  secondary_value?: number;
}

export interface ApplianceEnergyDatum {
  label: string;
  value_kwh: number;
  color: string;
}

export interface DashboardCharts {
  power_over_time: TimeSeriesPoint[];
  baseline_vs_ai: TimeSeriesPoint[];
  daily_savings: TimeSeriesPoint[];
  energy_by_appliance: ApplianceEnergyDatum[];
}

export interface DashboardData {
  classroom_id: UUID;
  classroom_name: string;
  building_name: string;
  connection: ConnectionStatus;
  last_updated_at: ISODateTime;
  metrics: DashboardMetric[];
  devices: DashboardDevice[];
  ai: AIInsight;
  charts: DashboardCharts;
}

export interface DeviceControlRequest {
  device_id: UUID;
  patch: Partial<
    Pick<
      DashboardDevice,
      "is_on" | "mode" | "target_temperature_c" | "fan_speed" | "brightness_pct"
    >
  >;
  reason: string;
}

export interface DashboardService {
  getInitialState(): Promise<DashboardData>;
  requestDeviceControl(request: DeviceControlRequest): Promise<{ request_id: UUID }>;
  setMockScenario?(scenario: MockScenario): void;
}
