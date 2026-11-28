"use client";

import { SolarDigitalTwin } from "@/src/components/SolarDigitalTwin";
import { TelemetryHistoryChart, type HistoryChartDefinition } from "@/src/components/TelemetryHistoryChart";
import { SOLAR_ENERGY_PRECISION, solarCollectedWh, solarTelemetryIsFresh } from "@/src/lib/prototype-energy";
import type { ReactNode } from "react";
import type {
  PrototypeConnection,
  PrototypeMonitorSnapshot,
  SolarFixedYaw,
  SolarTelemetry,
  SolarYawSequence,
} from "@/src/types/prototype-monitor";

type Feed = {
  snapshot: PrototypeMonitorSnapshot | null;
  history: SolarTelemetry[];
  error: string | null;
};

const stopReasons = {
  waiting_for_gps: "Waiting for GPS position and time",
  waiting_for_clock: "Waiting for network time",
  spa_error: "Solar position calculation failed",
  feedback_unavailable: "Axis feedback unavailable",
  zenith_limit: "Target zenith at or beyond 90°",
  local_hold: "Held by local operator",
};

const yawModes = { test: "Local yaw test", run: "Local yaw run" };
const yawPhases = { idle: "Idle", clockwise: "Clockwise", pause: "Pause", anticlockwise: "Anticlockwise" };
const yawResults = {
  completed: "Completed",
  user_stopped: "Stopped by operator",
  feedback_unavailable: "Axis feedback unavailable",
  sampling_interrupted: "Gyroscope sampling interrupted",
  wrong_direction: "Rotation in the wrong direction",
  stalled: "Rotation stalled",
  timed_out: "Timed out",
};

const fixedYawStatuses: Record<SolarFixedYaw["status"], string> = {
  waiting_for_clock: "Waiting for the solar clock",
  waiting_for_reference: "Waiting for the true-south reference",
  calibrating: "Calibrating the reference",
  ready: "Ready to arm",
  tracking: "Tracking the sun",
  aligned: "Aligned with target azimuth",
  sun_below_horizon: "Sun below the horizon",
  outside_travel: "Target outside yaw travel",
  feedback_unavailable: "Yaw feedback unavailable",
  reference_expired: "Reference expired",
  base_moved_during_calibration: "Base moved during calibration",
  sampling_interrupted: "Gyroscope sampling interrupted",
  control_delayed: "Control update delayed",
  wrong_direction: "Rotation in the wrong direction",
  stalled: "Rotation stalled",
};

function statusLabel(connection: PrototypeConnection | undefined, reachable: boolean) {
  if (!reachable || !connection || connection.availability === "unknown") return "Unknown";
  return connection.availability === "offline" ? "Offline" : "Online";
}

function freshnessLabel(connection: PrototypeConnection | undefined, brokerConnected: boolean) {
  if (!connection?.last_received_at) return "Waiting for data";
  return brokerConnected && connection.freshness === "fresh" && connection.availability !== "offline"
    ? "Receiving data"
    : "Stale data";
}

function timestamp(value: string | null | undefined) {
  if (!value || !Number.isFinite(Date.parse(value))) return "No data received";
  const date = new Date(value);
  return <time dateTime={value}>{date.toLocaleString()}</time>;
}

export function PrototypeStatus({ snapshot, error }: Pick<Feed, "snapshot" | "error">) {
  const reachable = !!snapshot && !error;
  const brokerConnected = reachable && snapshot.broker_connected;
  const brokerStatus = reachable ? (brokerConnected ? "Connected" : "Disconnected") : "Unknown";
  const devices = [
    { name: "Solar tracker ESP32", href: "#solar", destination: "Solar readings", connection: snapshot?.solar },
    { name: "Device control ESP32", href: "#devices", destination: "Relay controls", connection: snapshot?.relay },
  ];

  return (
    <section className="prototype-status" aria-labelledby="prototype-status-title">
      <div className="section-heading section-heading--compact">
        <h2 id="prototype-status-title">System status</h2>
      </div>
      <div className="prototype-status__grid">
        <article className="solar-panel">
          <h3>MQTT broker</h3>
          <p className={`status-dot status-dot--${brokerConnected ? "ok" : reachable ? "error" : "unknown"}`}>
            {brokerStatus}
          </p>
          {!reachable && <small>{error ?? "Connecting to the monitoring API"}</small>}
        </article>
        {devices.map(({ name, href, destination, connection }) => {
          const status = statusLabel(connection, reachable && (brokerConnected || connection?.availability === "offline"));
          const freshness = freshnessLabel(connection, brokerConnected);
          const tone = status === "Offline" ? "error"
            : freshness === "Stale data" ? "stale"
              : status === "Online" ? "ok" : "unknown";

          return (
            <article className="solar-panel" key={name}>
              <h3>{name}</h3>
              <p className={`status-dot status-dot--${tone}`}>{status} · {freshness}</p>
              <small>{connection?.last_received_at
                ? <>Last received {timestamp(connection.last_received_at)}</>
                : "No data received yet"}</small>
              <div className="prototype-status__links">
                <a href={href}>{destination}</a>
                {href === "#devices" && <a href="#appliances">Operating time</a>}
              </div>
            </article>
          );
        })}
      </div>
    </section>
  );
}

function reading(value: number | null | undefined, precision: number, unit: string) {
  return value === null || value === undefined || !Number.isFinite(value)
    ? "Unavailable"
    : `${value.toFixed(precision)}${unit === "°" ? "" : " "}${unit}`;
}

function yawSequenceFacts(sequence: SolarYawSequence | null | undefined): Array<[string, ReactNode]> {
  if (!sequence) return [];
  const facts: Array<[string, ReactNode]> = [["Yaw phase", yawPhases[sequence.phase]]];
  if (sequence.phase === "idle") facts.unshift(["Yaw sequence", yawModes[sequence.kind]]);
  if (sequence.leg_progress_deg !== null) facts.push(["Yaw leg progress", reading(sequence.leg_progress_deg, 1, "°")]);
  if (sequence.leg_target_deg !== null) facts.push(["Yaw leg target", reading(sequence.leg_target_deg, 1, "°")]);
  facts.push(["Net yaw rotation", reading(sequence.net_rotation_deg, 1, "°")]);
  if (sequence.result) facts.push(["Yaw result", yawResults[sequence.result]]);
  return facts;
}

function fixedYawFacts(yaw: SolarFixedYaw | null | undefined, hasTarget: boolean): Array<[string, ReactNode]> {
  if (!yaw) return [];
  const solarTime = yaw.solar_time
    ? <time dateTime={yaw.solar_time}>{yaw.solar_time.replace("T", " ")}</time>
    : yaw.clock_set ? "Set" : "Unset";
  const facts: Array<[string, ReactNode]> = [
    ["Yaw tracking status", fixedYawStatuses[yaw.status]],
    ["Solar clock", solarTime],
    ["Solar clock speed", `${yaw.demo_speed}×`],
    ["True-south reference", yaw.calibrating ? "Calibrating" : yaw.referenced ? "Set" : "Unset"],
    ["Yaw drive", yaw.armed ? "Armed" : "Disarmed"],
  ];
  if (hasTarget && yaw.clock_set && yaw.referenced && !yaw.calibrating) {
    facts.push(["Target within yaw travel", yaw.reachable ? "Yes" : "No"]);
  }
  return facts;
}

function motor(pwm: number | undefined) {
  if (pwm === undefined) return "Unavailable";
  return pwm === 0 ? "Stopped" : `Drive requested (${pwm}/255 PWM)`;
}

function sensor(available: boolean | undefined) {
  if (available === undefined) return "Unknown";
  return available ? "Detected" : "Unavailable";
}

function Facts({ items }: { items: Array<[string, ReactNode]> }) {
  return (
    <dl className="solar-facts">
      {items.map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{value}</dd></div>)}
    </dl>
  );
}

const targetColor = "#906019";
const actualColor = "#1a6b5f";

const solarEnergyHistoryChart: HistoryChartDefinition<SolarTelemetry> = {
  title: "Solar energy collected over time", unit: "Wh", precision: SOLAR_ENERGY_PRECISION, breakAt: "energy-reset",
  series: [{ label: "Energy collected", color: targetColor, get: solarCollectedWh }],
};

const charts: HistoryChartDefinition<SolarTelemetry>[] = [
  {
    title: "Azimuth", unit: "°", precision: 1, breakAt: "azimuth-wrap",
    series: [
      { label: "Target", color: targetColor, get: (sample) => sample.payload.tracking.target_azimuth },
      { label: "Actual", color: actualColor, get: (sample) => sample.payload.tracking.actual_azimuth },
    ],
  },
  {
    title: "Zenith", unit: "°", precision: 1,
    series: [
      { label: "Calculated or requested", color: targetColor, get: (sample) => sample.payload.tracking.target_zenith },
      { label: "Measured tilt", color: actualColor, get: (sample) => sample.payload.tracking.actual_zenith },
    ],
  },
  {
    title: "Solar voltage", unit: "V", precision: 2,
    series: [{ label: "Voltage", color: actualColor, get: (sample) => sample.payload.solar_power.voltage }],
  },
  {
    title: "Solar current", unit: "A", precision: 3,
    series: [{ label: "Current", color: actualColor, get: (sample) => sample.payload.solar_power.current }],
  },
  {
    title: "Solar power", unit: "W", precision: 2,
    series: [{ label: "Power", color: actualColor, get: (sample) => sample.payload.solar_power.power }],
  },
  solarEnergyHistoryChart,
];

export function SolarTracker({ snapshot, history, error }: Feed) {
  const solar = snapshot?.solar;
  const gpsEnabled = solar?.telemetry?.payload.gps.enabled !== false;
  const live = solarTelemetryIsFresh(snapshot, error) ? solar?.telemetry : null;
  const gps = live?.payload.gps;
  const tracking = live?.payload.tracking;
  const power = live?.payload.solar_power;
  const sensors = live?.payload.sensors;
  const lastFixMs = gps?.last_fix_age_ms;
  const stopReason = tracking?.stop_reason;
  const yawSequence = tracking?.yaw_sequence;
  const fixedYaw = tracking?.fixed_yaw;
  const stopLabel = stopReason && fixedYaw?.status !== stopReason
    ? fixedYaw && stopReason === "waiting_for_clock" ? fixedYawStatuses.waiting_for_clock : stopReasons[stopReason]
    : null;
  const trackingMode = !tracking ? "Unknown"
    : yawSequence && yawSequence.phase !== "idle" ? yawModes[yawSequence.kind]
    : fixedYaw ? "Fixed tilt · yaw only"
    : stopReason === "local_hold" ? "Local hold"
    : tracking.mode === "auto" ? "Automatic" : "Manual";
  const state = live ? "Live readings" : solar?.last_received_at ? "Stale readings" : "Waiting for solar telemetry";

  return (
    <>
      <section id="solar" aria-labelledby="solar-title">
        <div className="section-heading">
          <h2 id="solar-title">Solar tracker</h2>
        </div>
        <div className="connection-banner" role="status">
          <div>
            <strong>{state}</strong>
            <span>
              {live
                ? <>Latest sample received {timestamp(live.received_at)}</>
                : error ?? (solar?.last_received_at
                  ? <>Last sample received {timestamp(solar.last_received_at)}</>
                  : "Readings appear once the tracker publishes to this classroom.")}
            </span>
          </div>
        </div>

        <div className={`solar-panel-grid${gpsEnabled ? "" : " solar-panel-grid--without-gps"}`}>
          {gpsEnabled && (
            <article className="solar-panel">
              <h3>GPS</h3>
              <Facts items={[
                ["Position", gps ? gps.fix ? "Fix acquired" : "No fix" : "Unknown"],
                ["GPS time", gps ? gps.time_valid ? "Valid" : "Unavailable" : "Unknown"],
                ["Latitude", reading(gps?.fix ? gps.latitude : null, 5, "°")],
                ["Longitude", reading(gps?.fix ? gps.longitude : null, 5, "°")],
                ["Last GPS update", lastFixMs == null ? "Unknown" : `${(lastFixMs / 1000).toFixed(1)} s before sample`],
              ]} />
            </article>
          )}

          <article className="solar-panel">
            <h3>Tracking and motors</h3>
            <Facts items={[
              ["Tracking mode", trackingMode],
              ["Target azimuth", reading(tracking?.target_azimuth, 1, "°")],
              ["Actual azimuth", reading(tracking?.actual_azimuth, 1, "°")],
              [fixedYaw ? "Calculated solar zenith" : "Target zenith", reading(tracking?.target_zenith, 1, "°")],
              ["Actual zenith", reading(tracking?.actual_zenith, 1, "°")],
              ["Azimuth motor command", motor(tracking?.azimuth_pwm)],
              [fixedYaw ? "Tilt motor command" : "Zenith motor command", motor(tracking?.zenith_pwm)],
              ...(stopLabel ? [["Stop reason", stopLabel] as [string, ReactNode]] : []),
              ...fixedYawFacts(fixedYaw, tracking?.target_azimuth != null),
              ...yawSequenceFacts(yawSequence),
            ]} />
            {fixedYaw && (fixedYaw.status === "waiting_for_clock" || fixedYaw.status === "waiting_for_reference" || fixedYaw.status === "ready") && (
              <small>In Serial Monitor, set the clock with <code>tYYYYMMDDhhmmss</code>, face true south and send <code>h</code>, then send <code>a</code> to arm. Send <code>s</code> to stop.</small>
            )}
          </article>

          <article className="solar-panel">
            <h3>Solar electrical readings</h3>
            <Facts items={[
              ["Voltage", reading(power?.voltage, 2, "V")],
              ["Current", reading(power?.current, 3, "A")],
              ["Power", reading(power?.power, 2, "W")],
              ["Energy collected", reading(solarCollectedWh(live), solarEnergyHistoryChart.precision, "Wh")],
              ["INA219", sensor(sensors?.ina219_available)],
              ["Azimuth MPU", sensor(sensors?.azimuth_mpu_available)],
              ["Zenith MPU", sensor(sensors?.zenith_mpu_available)],
            ]} />
          </article>
        </div>
      </section>

      <section aria-labelledby="solar-history-title">
        <div className="section-heading">
          <h2 id="solar-history-title">Recent solar history</h2>
        </div>
        <div className="solar-chart-grid">
          {charts.map((definition) => <TelemetryHistoryChart key={definition.title} definition={definition} history={history} />)}
        </div>
      </section>

      <SolarDigitalTwin snapshot={snapshot} error={error} />

      <PrototypeStatus snapshot={snapshot} error={error} />
    </>
  );
}
