import type { ClassroomTelemetryView } from "./classroom-telemetry";
import type { DashboardMetric } from "../types/dashboard";
import type { ClassroomMeterPoint, PrototypeMonitorSnapshot, SolarTelemetry } from "../types/prototype-monitor";

export const SOLAR_ENERGY_PRECISION = 6;

type EnergyChartPoint = {
  received_at: string;
  payload: { boot_id: string | null };
  meter: "classroom" | "solar";
  powerW: number | null;
  energyWh: number | null;
};

export function energyChartHistory(classroom: ClassroomMeterPoint[], solar: SolarTelemetry[]): EnergyChartPoint[] {
  const samples: EnergyChartPoint[] = [
    ...classroom.map((sample): EnergyChartPoint => ({
      received_at: sample.received_at,
      payload: { boot_id: sample.boot_id },
      meter: "classroom",
      powerW: sample.active_power_w,
      energyWh: sample.energy_wh_total,
    })),
    ...solar.map((sample): EnergyChartPoint => ({
      received_at: sample.received_at,
      payload: { boot_id: sample.payload.boot_id },
      meter: "solar",
      powerW: sample.payload.sensors.ina219_available ? sample.payload.solar_power.power : null,
      energyWh: solarCollectedWh(sample),
    })),
  ];
  return samples
    .map((sample) => ({ sample, time: Date.parse(sample.received_at) }))
    .filter(({ time }) => Number.isFinite(time))
    .sort((a, b) => a.time - b.time)
    .map(({ sample }) => sample);
}

export function solarCollectedWh(sample: SolarTelemetry | null | undefined): number | null {
  const value = sample?.payload.solar_power.energy;
  return sample?.payload.sensors.ina219_available && value != null && Number.isFinite(value) ? value : null;
}

export function solarTelemetryIsFresh(snapshot: PrototypeMonitorSnapshot | null, error: string | null): boolean {
  const solar = snapshot?.solar;
  return !error && snapshot?.broker_connected === true && !!solar?.telemetry &&
    solar.freshness === "fresh" && solar.availability !== "offline";
}

export function accumulatedSolarEnergyMetric(
  snapshot: PrototypeMonitorSnapshot | null,
  error: string | null,
  fallbackUpdatedAt: string,
): DashboardMetric {
  const sample = snapshot?.solar.telemetry;
  const receivedAt = sample?.received_at;
  const value = solarCollectedWh(sample);

  return {
    id: "solar_energy",
    label: "Accumulated solar energy",
    value,
    display_value: value === null ? "--" : value.toFixed(SOLAR_ENERGY_PRECISION),
    unit: "Wh",
    detail: value === null ? "Measurement unavailable" : "Collected since tracker restart or reset",
    trend: "steady",
    trend_label: receivedAt ? `Received ${new Date(receivedAt).toLocaleTimeString()}` : "Waiting for solar telemetry",
    status: value === null ? "unknown" : solarTelemetryIsFresh(snapshot, error) ? "ok" : "stale",
    updated_at: receivedAt ?? fallbackUpdatedAt,
  };
}

export function estimatedEnergySavedMetric(
  snapshot: PrototypeMonitorSnapshot | null,
  classroom: ClassroomTelemetryView,
  fallbackUpdatedAt: string,
): DashboardMetric {
  const solarSample = snapshot?.solar.telemetry;
  const classroomSample = classroom.reading;
  const collectedWh = solarCollectedWh(solarSample);
  const consumedWh = classroomSample?.payload.energy_wh_total;
  const metric: DashboardMetric = {
    id: "energy_saved",
    label: "Estimated energy saved",
    value: null,
    display_value: "--",
    unit: "Wh",
    detail: "Solar energy collected − classroom energy consumed",
    trend: "steady",
    trend_label: "Waiting for readings from both energy meters",
    status: "unknown",
    updated_at: fallbackUpdatedAt,
  };
  if (!solarSample || !classroomSample?.payload.sensors.ina219_available ||
    collectedWh === null || consumedWh == null || !Number.isFinite(consumedWh)) {
    return metric;
  }
  if (!classroom.fresh || !solarTelemetryIsFresh(snapshot, classroom.error)) {
    return { ...metric, status: "stale", trend_label: "Waiting for fresh readings from both meters" };
  }

  const value = collectedWh - consumedWh;
  const updatedAt = Date.parse(solarSample.received_at) > Date.parse(classroomSample.received_at)
    ? solarSample.received_at
    : classroomSample.received_at;
  return {
    ...metric,
    value,
    display_value: value.toFixed(3),
    detail: `${collectedWh.toFixed(3)} Wh solar − ${consumedWh.toFixed(3)} Wh classroom`,
    trend_label: "Meter totals may cover different periods",
    status: "ok",
    updated_at: updatedAt,
  };
}
