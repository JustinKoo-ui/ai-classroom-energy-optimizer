"use client";

import { useEffect, useState, useSyncExternalStore } from "react";
import Skeleton from "react-loading-skeleton";
import "react-loading-skeleton/dist/skeleton.css";
import { AIInsightPanel } from "@/src/components/AIInsightPanel";
import { CameraPreview } from "@/src/components/CameraPreview";
import { DeviceControlCard } from "@/src/components/DeviceControlCard";
import { EnergyCharts } from "@/src/components/EnergyCharts";
import { EnvironmentalAnalyst } from "@/src/components/EnvironmentalAnalyst";
import { MetricCard } from "@/src/components/MetricCard";
import { useDashboard } from "@/src/hooks/useDashboard";
import { useLocalHardwareControls } from "@/src/hooks/useLocalHardwareControls";
import { useEnvironmentTelemetry } from "@/src/hooks/useEnvironmentTelemetry";
import { useOccupancy } from "@/src/hooks/useOccupancy";
import { withHardwareState } from "@/src/api/hardware-control-service";
import type { DashboardMetric } from "@/src/types/dashboard";

const navigationItems = [
  { id: "overview", label: "Overview" },
  { id: "camera", label: "Camera" },
  { id: "devices", label: "Devices" },
  { id: "analytics", label: "Analytics" },
  { id: "ai-insights", label: "AI insights" },
  { id: "environmental-analyst", label: "Environmental analyst" },
] as const;

function subscribeToHashChange(onChange: () => void) {
  window.addEventListener("hashchange", onChange);
  return () => window.removeEventListener("hashchange", onChange);
}

function getActiveSection() {
  const sectionId = window.location.hash.slice(1);
  return navigationItems.find((item) => item.id === sectionId)?.id ?? "overview";
}

export function Dashboard() {
  const activeSection = useSyncExternalStore(subscribeToHashChange, getActiveSection, () => "overview");
  const { data, loading, error, requestControl, setScenario } = useDashboard();
  const environment = useEnvironmentTelemetry();
  const occupancy = useOccupancy(data?.classroom_id);
  const hardware = useLocalHardwareControls(data?.classroom_id);
  const sensorFresh = isEnvironmentTelemetryFresh(environment.telemetry);
  const [clockNow, setClockNow] = useState<number | null>(null);
  const urgencyActive = hardware.urgency?.status === "monitoring";
  const urgencySecondsRemaining =
    urgencyActive && hardware.urgency?.expires_at && clockNow !== null
      ? Math.max(0, Math.ceil((Date.parse(hardware.urgency.expires_at) - clockNow) / 1000))
      : null;

  useEffect(() => {
    if (!urgencyActive) return;
    const timer = window.setInterval(() => setClockNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [urgencyActive]);

  if (loading && !data) {
    return (
      <main className="dashboard-shell dashboard-loading" aria-busy="true">
        <Skeleton height={64} borderRadius={18} />
        <div className="metric-grid">
          {Array.from({ length: 7 }).map((_, index) => (
            <Skeleton key={index} height={210} borderRadius={22} />
          ))}
        </div>
      </main>
    );
  }

  if (!data) {
    return (
      <main className="dashboard-shell">
        <div className="inline-alert" role="alert">
          {error ?? "No classroom data is available."} Retrying automatically.
        </div>
      </main>
    );
  }

  const displayedDevices = hardware.enabled
    ? data.devices.map((device) => withHardwareState(device, hardware.deviceStates?.devices, hardware.error, hardware.pendingDeviceIds))
    : data.devices;
  const handleDeviceControl = hardware.enabled ? hardware.requestControl : requestControl;

  return (
    <div className="app-frame">
      <aside className="sidebar">
        <a className="brand" href="#top" aria-label="Nexergy dashboard home">
          <span className="brand__mark">N</span>
          <span>Nexergy</span>
        </a>
        <nav aria-label="Primary navigation">
          {navigationItems.map((item) => (
            <a
              key={item.id}
              className={activeSection === item.id ? "is-active" : undefined}
              href={`#${item.id}`}
              aria-current={activeSection === item.id ? "location" : undefined}
            >
              {item.label}
            </a>
          ))}
        </nav>
        <div className="sidebar__foot">
          <span>{hardware.mode === "remote" ? "Cloudflare control" : hardware.enabled ? "Local control" : "Prototype mode"}</span>
          <strong>{hardware.enabled ? "Policy gate active" : "No hardware path"}</strong>
          <small>AI advisory only</small>
        </div>
      </aside>

      <main className="dashboard-shell" id="top">
        <header className="topbar">
          <div>
            <p className="eyebrow">{data.building_name}</p>
            <h1>{data.classroom_name}</h1>
          </div>
          <div className="topbar__actions">
            {hardware.enabled && (
              <div className="urgency-control">
                {hardware.canActivateUrgency && <button
                  className="urgency-control__button"
                  type="button"
                  disabled={urgencyActive}
                  onClick={() => void hardware.activateUrgency()}
                >
                  {urgencyActive ? "Urgency monitoring" : "Activate urgency"}
                </button>}
                <span role="status" aria-live="polite">
                  {urgencyActive && urgencySecondsRemaining !== null
                    ? `${formatCountdown(urgencySecondsRemaining)} remaining`
                    : urgencyStatusLabel(hardware.urgency?.status)}
                </span>
              </div>
            )}
            <label className="scenario-picker">
              <span>Demo state</span>
              <select defaultValue="live" onChange={(event) => setScenario(event.target.value as "live" | "device_offline" | "connection_loss")}>
                <option value="live">Demo panels: live</option>
                <option value="device_offline">Fan disconnected</option>
                <option value="connection_loss">Demo feed offline</option>
              </select>
            </label>
            <span className={`connection-pill connection-pill--${sensorFresh ? "connected" : "disconnected"}`}>
              <i /> {sensorFresh ? "Live sensors" : environment.loaded ? "Sensor feed unavailable" : "Connecting sensors"}
            </span>
            <button className="avatar" type="button" aria-label="Open user menu">JM</button>
          </div>
        </header>

        {error && <div className="inline-alert" role="alert">{error}</div>}
        {hardware.enabled && hardware.error && (
          <div className="inline-alert" role="alert">{hardware.error}</div>
        )}
        {data.connection === "disconnected" && (
          <div className="connection-banner" role="status">
            <div><strong>Demo dashboard feed unavailable</strong><span>Live environmental sensors continue polling independently.</span></div>
            <span>Demo panels will retry automatically.</span>
          </div>
        )}

        <section id="overview" aria-labelledby="overview-title">
          <div className="section-heading">
            <div>
              <p className="eyebrow">Room pulse</p>
              <h2 id="overview-title">Live overview</h2>
            </div>
            <p>
              {environment.telemetry?.observed_at
                ? `Sensor sample ${new Date(environment.telemetry.observed_at).toLocaleTimeString()} · other dashboard panels use demo data`
                : "Waiting for live sensors · other dashboard panels use demo data"}
            </p>
          </div>
          <div className="metric-grid">
            {data.metrics.map((metric) => (
              <MetricCard
                key={metric.id}
                metric={withLiveOccupancyReading(
                  withLiveEnvironmentReading(metric, environment),
                  occupancy,
                )}
              />
            ))}
          </div>
        </section>

        <CameraPreview key={process.env.NEXT_PUBLIC_CLASSROOM_ID ?? data.classroom_id} classroomId={process.env.NEXT_PUBLIC_CLASSROOM_ID ?? data.classroom_id} />

        <div className="operations-grid">
          <section id="devices" aria-labelledby="device-title">
            <div className="section-heading">
              <div>
                <p className="eyebrow">Deterministic control</p>
                <h2 id="device-title">Device control</h2>
              </div>
              <p>
                {hardware.enabled
                  ? "Local policy checks precede MQTT dispatch."
                  : "Demo interactions do not dispatch commands."}
              </p>
            </div>
            <div className="connection-banner" role="status">
              {hardware.enabled ? (
                <div>
                  <strong>{hardware.mode === "remote" ? "Authenticated Cloudflare ESP32 controls" : "Local ESP32 controls"} · digital ON/OFF only</strong>
                  <span>
                    Auto/Manual and relay state come from the controller. Commands wait for ESP32 acknowledgement.
                    PIR motion: {sensorFresh && environment.telemetry?.motion_detected !== null
                      ? environment.telemetry?.motion_detected ? "detected" : "not detected"
                      : "unknown"}.
                    Fan speed, brightness, AC setpoint, and wattage are unsupported/demo-only.
                  </span>
                </div>
              ) : (
                <div>
                  <strong>Demo controls · no hardware commands are sent</strong>
                  <span>Simulated controls on this page do not operate relays. Hardware controls require the configured local connection or authenticated Cloudflare control connection.</span>
                </div>
              )}
            </div>
            <div className="device-grid">
              {displayedDevices.map((device) => <DeviceControlCard key={device.id} device={device} onRequest={handleDeviceControl} compact />)}
            </div>
          </section>
          <section id="ai-insights" aria-labelledby="ai-title">
            <div className="section-heading section-heading--compact">
              <div><p className="eyebrow">Anonymous tracking</p><h2 id="ai-title">AI insights</h2></div>
            </div>
            <AIInsightPanel live={occupancy} />
          </section>
        </div>

        <section id="analytics" className="analytics-section" aria-labelledby="analytics-title">
          <div className="section-heading">
            <div><p className="eyebrow">Demo data</p><h2 id="analytics-title">Energy analytics</h2></div>
            <p>Illustrative only · no calibrated power or energy meter is connected</p>
          </div>
          <EnergyCharts charts={data.charts} />
        </section>

        <EnvironmentalAnalyst classroomId={data.classroom_id} />

        <footer className="dashboard-footer">
          <span>Nexergy · Classroom Energy Intelligence</span>
          <span>AI recommends. Deterministic policy decides. Devices acknowledge.</span>
        </footer>
      </main>
    </div>
  );
}

function formatCountdown(seconds: number): string {
  const minutes = Math.floor(seconds / 60);
  const remainder = seconds % 60;
  return `${minutes}:${String(remainder).padStart(2, "0")}`;
}

function urgencyStatusLabel(status: string | undefined): string {
  switch (status) {
    case "completed_no_presence":
      return "Completed · no person detected";
    case "completed_presence":
      return "Completed · returning to policy";
    case "completed_camera_unknown":
      return "Completed · camera state unknown";
    default:
      return "Urgency inactive";
  }
}

function withLiveOccupancyReading(
  metric: DashboardMetric,
  live: ReturnType<typeof useOccupancy>,
): DashboardMetric {
  if (metric.id !== "occupancy" || !live.enabled) return metric;

  const reading = live.occupancy;
  const observedAt = reading?.observed_at ?? null;
  const ageMs = observedAt ? Date.now() - Date.parse(observedAt) : null;
  const fresh =
    reading?.freshness === "fresh" &&
    ageMs !== null &&
    ageMs >= 0 &&
    ageMs <= 15_000;
  const value = fresh ? reading.count : null;
  const status: DashboardMetric["status"] = !live.loaded
    ? "loading"
    : live.error && !reading
      ? "error"
      : reading?.freshness === "stale" || (ageMs !== null && ageMs > 15_000)
        ? "stale"
        : fresh && value !== null
          ? "ok"
          : "unknown";
  const detail = value === null
    ? live.error ?? (reading?.freshness === "stale"
      ? "Camera reading is stale; count hidden."
      : "Waiting for a fresh camera reading.")
    : reading?.status === "possibly_vacant"
      ? "No people detected; vacancy not yet confirmed."
      : reading?.confidence !== null && reading?.confidence !== undefined
        ? `Live camera estimate · ${Math.round(reading.confidence * 100)}% confidence`
        : "Live camera estimate";

  return {
    ...metric,
    value,
    display_value: value === null ? "--" : String(value),
    unit: value === 1 ? "person" : "people",
    detail,
    trend: "steady",
    trend_label: observedAt
      ? `Observed ${new Date(observedAt).toLocaleTimeString()}`
      : "No camera reading yet",
    status,
    updated_at: observedAt ?? metric.updated_at,
  };
}

function withLiveEnvironmentReading(
  metric: DashboardMetric,
  environment: ReturnType<typeof useEnvironmentTelemetry>,
): DashboardMetric {
  let value: number | null;
  let unit: string;
  let precision: number;
  let detail: string;
  if (metric.id === "temperature") {
    value = environment.telemetry?.temperature_c ?? null;
    unit = "°C";
    precision = 1;
    detail = "Live DHT22 reading";
  } else if (metric.id === "humidity") {
    value = environment.telemetry?.relative_humidity_pct ?? null;
    unit = "%";
    precision = 1;
    detail = "Live DHT22 reading";
  } else if (metric.id === "ambient_light") {
    const rawAdc = environment.telemetry?.illuminance_adc;
    const lux = environment.telemetry?.illuminance_lux;
    value = rawAdc ?? lux ?? null;
    unit = rawAdc !== null && rawAdc !== undefined ? "ADC counts" : "lux";
    precision = rawAdc !== null && rawAdc !== undefined ? 0 : 1;
    detail =
      rawAdc !== null && rawAdc !== undefined
        ? "LM393 analog output; not calibrated to lux"
        : "Calibrated sensor reading";
  } else {
    return metric;
  }

  const observedAt = environment.telemetry?.observed_at;
  const isFresh = isEnvironmentTelemetryFresh(environment.telemetry);
  const unavailableMessage =
    environment.error ??
    (environment.loaded ? "Waiting for the first sensor reading." : "Connecting to sensors.");
  const status: DashboardMetric["status"] = !environment.loaded
    ? "loading"
    : value === null
      ? "error"
      : isFresh
        ? "ok"
        : "stale";

  return {
    ...metric,
    value,
    display_value: value === null ? "--" : value.toFixed(precision),
    unit,
    detail: value === null ? unavailableMessage : detail,
    trend: "steady",
    trend_label: observedAt
      ? `Observed ${new Date(observedAt).toLocaleTimeString()}`
      : "No sensor reading",
    status,
    updated_at: observedAt ?? metric.updated_at,
  };
}

function isEnvironmentTelemetryFresh(
  telemetry: ReturnType<typeof useEnvironmentTelemetry>["telemetry"],
): boolean {
  const observedAt = telemetry?.observed_at;
  if (!observedAt) return false;
  const ageMs = Date.now() - Date.parse(observedAt);
  const maxAgeMs = Math.max(telemetry?.sample_interval_ms ?? 5000, 2000) * 3;
  return ageMs >= 0 && ageMs <= maxAgeMs;
}
