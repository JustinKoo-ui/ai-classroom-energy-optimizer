import type { ClassroomMeterPoint, PrototypeMonitorSnapshot } from "../types/prototype-monitor";

const HISTORY_LIMIT = 120;

export function classroomTelemetryView(
  snapshot: PrototypeMonitorSnapshot | null,
  error: string | null,
  now: number | null,
) {
  const reading = snapshot?.classroom_telemetry ?? null;
  const sampleAgeMs = reading && now !== null
    ? Math.max(0, now - Date.parse(reading.received_at))
    : null;
  const recent = snapshot?.classroom_telemetry_freshness === "fresh" &&
    reading !== null &&
    (sampleAgeMs === null || sampleAgeMs <= Math.max(reading.payload.sample_interval_ms, 2_000) * 3);
  const relayAvailability = snapshot?.relay.availability ?? "unknown";
  const availability = !error && snapshot?.broker_connected
    ? relayAvailability === "unknown" && recent ? "online" : relayAvailability
    : "unknown";
  const fresh = availability === "online" && recent;
  return { reading, availability, fresh, sampleAgeMs, error };
}

export type ClassroomTelemetryView = ReturnType<typeof classroomTelemetryView>;

export function mergeClassroomMeterHistory(
  current: ClassroomMeterPoint[],
  snapshot: PrototypeMonitorSnapshot,
): ClassroomMeterPoint[] {
  const reading = snapshot.classroom_telemetry;
  const fresh = snapshot.classroom_telemetry_freshness === "fresh";
  const persisted = snapshot.classroom_meter_history;
  const legacy = persisted == null ? snapshot.energy_history : null;
  if (!persisted && !legacy && (!fresh || !reading || reading.received_at === current.at(-1)?.received_at)) {
    return current;
  }

  const samples = new Map(current.map((sample) => [sample.received_at, sample]));
  for (const sample of legacy ?? []) {
    if (!samples.has(sample.received_at)) {
      samples.set(sample.received_at, { ...sample, boot_id: null, active_power_w: null });
    }
  }
  for (const sample of persisted ?? []) samples.set(sample.received_at, sample);
  if (fresh && reading) {
    const { boot_id, sensors, active_power_w, energy_wh_total } = reading.payload;
    samples.set(reading.received_at, {
      received_at: reading.received_at,
      boot_id,
      active_power_w: sensors.ina219_available ? active_power_w : null,
      energy_wh_total: sensors.ina219_available ? energy_wh_total : null,
    });
  }
  return [...samples.values()]
    .sort((a, b) => Date.parse(a.received_at) - Date.parse(b.received_at))
    .slice(-HISTORY_LIMIT);
}
