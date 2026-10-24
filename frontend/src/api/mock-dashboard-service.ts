import type {
  DashboardData,
  DashboardService,
  DeviceControlRequest,
  MockScenario,
} from "@/src/types/dashboard";
import type { UUID } from "@/src/types/contracts";

const wait = (milliseconds: number) =>
  new Promise<void>((resolve) => window.setTimeout(resolve, milliseconds));

const makeId = (): UUID => crypto.randomUUID();
const now = () => new Date().toISOString();

function createInitialDashboard(): DashboardData {
  const timestamp = now();
  return {
    classroom_id: "26e97d46-7a34-4439-922a-83684bda557d",
    classroom_name: "Room A-204",
    building_name: "Engineering Block",
    connection: "connected",
    last_updated_at: timestamp,
    metrics: [
      {
        id: "occupancy",
        label: "Occupancy",
        value: 18,
        display_value: "18",
        unit: "people",
        detail: "60% of room capacity",
        trend: "steady",
        trend_label: "Stable for 12 min",
        status: "ok",
        updated_at: timestamp,
      },
      {
        id: "temperature",
        label: "Temperature",
        value: null,
        display_value: "--",
        unit: "°C",
        detail: "Waiting for live DHT22 data",
        trend: "down",
        trend_label: "No sensor reading",
        status: "loading",
        updated_at: timestamp,
      },
      {
        id: "humidity",
        label: "Humidity",
        value: null,
        display_value: "--",
        unit: "%",
        detail: "Waiting for live DHT22 data",
        trend: "steady",
        trend_label: "No sensor reading",
        status: "loading",
        updated_at: timestamp,
      },
      {
        id: "ambient_light",
        label: "Ambient light",
        value: null,
        display_value: "--",
        unit: "ADC counts",
        detail: "Waiting for live LDR data",
        trend: "up",
        trend_label: "No sensor reading",
        status: "loading",
        updated_at: timestamp,
      },
      {
        id: "current_power",
        label: "Current power",
        value: 2.84,
        display_value: "2.84",
        unit: "kW",
        detail: "Across 3 active devices",
        trend: "down",
        trend_label: "8% below baseline",
        status: "ok",
        updated_at: timestamp,
      },
      {
        id: "energy_today",
        label: "Energy used today",
        value: 19.6,
        display_value: "19.6",
        unit: "kWh",
        detail: "Since 07:00",
        trend: "up",
        trend_label: "4.1 kWh this hour",
        status: "ok",
        updated_at: timestamp,
      },
      {
        id: "energy_saved",
        label: "Estimated energy saved",
        value: 5.7,
        display_value: "5.7",
        unit: "kWh",
        detail: "RM 2.92 estimated value",
        trend: "up",
        trend_label: "22.5% vs baseline",
        status: "ok",
        updated_at: timestamp,
      },
    ],
    devices: [
      {
        id: "1b9f56b8-2c8a-40dd-87ae-b8e17357fb11",
        name: "Air conditioner",
        location: "North ceiling unit",
        kind: "air_conditioner",
        is_on: true,
        mode: "auto",
        connected: true,
        pending: false,
        target_temperature_c: 26,
        fan_speed: null,
        brightness_pct: null,
        active_power_w: 2260,
        status_label: "Cooling gently",
        updated_at: timestamp,
      },
      {
        id: "9e88b257-15d5-4362-a786-3260ab5947a0",
        name: "Ceiling fans",
        location: "4-zone array",
        kind: "fan",
        is_on: true,
        mode: "auto",
        connected: true,
        pending: false,
        target_temperature_c: null,
        fan_speed: 2,
        brightness_pct: null,
        active_power_w: 290,
        status_label: "Speed 2 · balanced",
        updated_at: timestamp,
      },
      {
        id: "68557d5f-6a3f-45f2-bdb6-d7798f19d5c8",
        name: "Lighting",
        location: "Window + aisle zones",
        kind: "lighting",
        is_on: true,
        mode: "auto",
        connected: true,
        pending: false,
        target_temperature_c: null,
        fan_speed: null,
        brightness_pct: 58,
        active_power_w: 290,
        status_label: "Daylight compensation",
        updated_at: timestamp,
      },
    ],
    ai: {
      occupants_detected: 18,
      departure_probability: 0.27,
      predicted_occupancy_trend: "stable",
      ai_control_state: "observing",
      prediction_confidence: 0.91,
      recommendation: "Maintain current comfort settings; departure is not yet likely.",
      model_version: "leave-v1.2.0",
      expires_at: new Date(Date.now() + 15_000).toISOString(),
    },
    charts: {
      power_over_time: [
        { label: "08:00", value: 1.2 },
        { label: "09:00", value: 2.7 },
        { label: "10:00", value: 3.1 },
        { label: "11:00", value: 2.9 },
        { label: "12:00", value: 1.5 },
        { label: "13:00", value: 2.2 },
        { label: "14:00", value: 2.84 },
      ],
      baseline_vs_ai: [
        { label: "Mon", value: 29.2, secondary_value: 23.7 },
        { label: "Tue", value: 31.4, secondary_value: 24.9 },
        { label: "Wed", value: 28.8, secondary_value: 22.4 },
        { label: "Thu", value: 33.1, secondary_value: 26.2 },
        { label: "Fri", value: 25.3, secondary_value: 19.6 },
      ],
      daily_savings: [
        { label: "Mon", value: 5.5 },
        { label: "Tue", value: 6.5 },
        { label: "Wed", value: 6.4 },
        { label: "Thu", value: 6.9 },
        { label: "Fri", value: 5.7 },
      ],
      energy_by_appliance: [
        { label: "Air conditioning", value_kwh: 14.1, color: "#143c36" },
        { label: "Fans", value_kwh: 2.2, color: "#7abf72" },
        { label: "Lighting", value_kwh: 3.3, color: "#d8f06a" },
      ],
    },
  };
}

export class MockDashboardService implements DashboardService {
  private state = createInitialDashboard();
  private scenario: MockScenario = "live";
  private firstRead = true;

  async getInitialState(): Promise<DashboardData> {
    if (this.firstRead) {
      this.firstRead = false;
      await wait(700);
    } else if (this.scenario !== "connection_loss") {
      this.tick();
    }
    return structuredClone(this.state);
  }

  async requestDeviceControl(
    request: DeviceControlRequest,
  ): Promise<{ request_id: UUID }> {
    const requestId = makeId();
    const deviceIndex = this.state.devices.findIndex((device) => device.id === request.device_id);
    if (deviceIndex < 0) throw new Error("Device not found");
    if (!this.state.devices[deviceIndex].connected) throw new Error("Device is disconnected");

    this.state.devices[deviceIndex] = {
      ...this.state.devices[deviceIndex],
      pending: true,
      status_label: "Awaiting policy approval",
      updated_at: now(),
    };
    await wait(650);

    this.state.devices[deviceIndex] = {
      ...this.state.devices[deviceIndex],
      ...request.patch,
      pending: false,
      status_label: "Applied · policy approved",
      updated_at: now(),
    };
    return { request_id: requestId };
  }

  setMockScenario(scenario: MockScenario): void {
    this.scenario = scenario;
    this.applyScenario();
  }

  private tick(): void {
    if (this.scenario === "connection_loss") {
      this.applyScenario();
      return;
    }
    const timestamp = now();
    const drift = Math.sin(Date.now() / 9_000);
    const updateMetric = (id: string, value: number, display: string) => {
      const metric = this.state.metrics.find((item) => item.id === id);
      if (metric) Object.assign(metric, { value, display_value: display, updated_at: timestamp });
    };
    const power = Math.max(1.2, 2.78 + drift * 0.16);
    updateMetric("current_power", power, power.toFixed(2));
    this.state.last_updated_at = timestamp;
    this.state.ai.expires_at = new Date(Date.now() + 15_000).toISOString();
    this.applyScenario();
  }

  private applyScenario(): void {
    this.state.connection = this.scenario === "connection_loss" ? "disconnected" : "connected";
    const fan = this.state.devices.find((device) => device.kind === "fan");
    if (fan) {
      fan.connected = this.scenario !== "device_offline";
      fan.status_label = fan.connected ? "Speed 2 · balanced" : "Controller disconnected";
    }
    if (this.scenario === "connection_loss") {
      for (const metric of this.state.metrics) metric.status = "stale";
    } else {
      for (const metric of this.state.metrics) metric.status = "ok";
    }
  }

}
