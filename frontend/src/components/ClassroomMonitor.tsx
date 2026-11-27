import type { ReactNode } from "react";
import type { ClassroomTelemetryView } from "@/src/lib/classroom-telemetry";

function Facts({ items }: { items: Array<[string, ReactNode]> }) {
  return (
    <dl className="solar-facts">
      {items.map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{value}</dd></div>)}
    </dl>
  );
}
function measurement(value: number | null | undefined, unit: string, precision: number) {
  return value == null || !Number.isFinite(value) ? "Unavailable" : `${value.toFixed(precision)} ${unit}`;
}

function sensor(available: boolean | undefined) {
  if (available === undefined) return "Unknown";
  return available ? "Available" : "Unavailable";
}

function SampleStatus({ view }: { view: ClassroomTelemetryView }) {
  const { availability, error, fresh, reading } = view;
  const status = availability === "online" ? "ESP32 online" : availability === "offline" ? "ESP32 offline" : "ESP32 status unknown";
  const freshness = fresh ? "Live readings" : reading ? "Stale readings" : "Waiting for telemetry";
  const tone = fresh ? "ok" : availability === "offline" ? "error" : reading ? "stale" : "unknown";
  return (
    <div className="classroom-monitor__sample" role="status">
      <span className={`status-dot status-dot--${tone}`}>{status} · {freshness}</span>
      {reading && <small>Last received <time dateTime={reading.received_at}>{new Date(reading.received_at).toLocaleString()}</time></small>}
      {error && <small>{error}</small>}
    </div>
  );
}

export function ClassroomSensorStatus({ view }: { view: ClassroomTelemetryView }) {
  const payload = view.reading?.payload;
  const sensors = payload?.sensors;
  const motion = payload?.motion_detected;
  const motionLabel = motion == null
    ? sensors?.pir_ready === false && view.fresh ? "Warming up" : "Unknown"
    : `${motion ? "Detected" : "Not detected"}${view.fresh ? "" : " (stale)"}`;
  const lightStatus = sensors && !sensors.bh1750_fresh && payload?.illuminance_lux != null
    ? sensors.bh1750_available ? "Last valid reading cached" : "Unavailable · cached reading"
    : sensor(sensors?.bh1750_available);

  return (
    <article className="solar-panel classroom-monitor" aria-labelledby="classroom-sensor-status-title">
      <h3 id="classroom-sensor-status-title">Classroom sensor status</h3>
      <SampleStatus view={view} />
      <Facts items={[
        ["PIR motion", motionLabel],
        ["DHT22", sensor(sensors?.dht22_available)],
        ["BH1750", lightStatus],
        ["ESP32 uptime", measurement(payload == null ? null : payload.uptime_ms / 1_000, "s", 0)],
      ]} />
    </article>
  );
}
export function ClassroomRelayCommands({ view }: { view: ClassroomTelemetryView }) {
  const relays = view.reading?.payload.relays;
  const command = (on: boolean | undefined) => on === undefined ? "Unknown" : on ? "ON" : "OFF";

  return (
    <article className="solar-panel classroom-monitor" aria-labelledby="classroom-relay-title">
      <h3 id="classroom-relay-title">ESP32 relay commands</h3>
      <small>Reported output commands. Appliance operation requires separate feedback.</small>
      <SampleStatus view={view} />
      <Facts items={[
        ["Light", command(relays?.light)],
        ["Fan", command(relays?.fan)],
        ["Air conditioner", command(relays?.air_conditioner)],
      ]} />
    </article>
  );
}
