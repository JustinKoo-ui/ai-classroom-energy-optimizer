"use client";

import Skeleton from "react-loading-skeleton";
import "react-loading-skeleton/dist/skeleton.css";
import { AIInsightPanel } from "@/src/components/AIInsightPanel";
import { DeviceControlCard } from "@/src/components/DeviceControlCard";
import { EnergyCharts } from "@/src/components/EnergyCharts";
import { MetricCard } from "@/src/components/MetricCard";
import { useDashboard } from "@/src/hooks/useDashboard";

export function Dashboard() {
  const { data, loading, error, requestControl, setScenario } = useDashboard();

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

  return (
    <div className="app-frame">
      <aside className="sidebar">
        <a className="brand" href="#top" aria-label="Nexergy dashboard home">
          <span className="brand__mark">N</span>
          <span>Nexergy</span>
        </a>
        <nav aria-label="Primary navigation">
          <a className="is-active" href="#overview">Overview</a>
          <a href="#devices">Devices</a>
          <a href="#analytics">Analytics</a>
          <a href="#ai-insights">AI insights</a>
        </nav>
        <div className="sidebar__foot">
          <span>Safety layer</span>
          <strong>Policy active</strong>
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
            <label className="scenario-picker">
              <span>Demo state</span>
              <select defaultValue="live" onChange={(event) => setScenario(event.target.value as "live" | "sensor_error" | "device_offline" | "connection_loss")}>
                <option value="live">Live operation</option>
                <option value="sensor_error">Lux sensor fault</option>
                <option value="device_offline">Fan disconnected</option>
                <option value="connection_loss">Data feed offline</option>
              </select>
            </label>
            <span className={`connection-pill connection-pill--${data.connection}`}>
              <i /> {data.connection === "connected" ? "Live telemetry" : "Reconnecting"}
            </span>
            <button className="avatar" type="button" aria-label="Open user menu">JM</button>
          </div>
        </header>

        {error && <div className="inline-alert" role="alert">{error}</div>}
        {data.connection === "disconnected" && (
          <div className="connection-banner" role="status">
            <div><strong>Classroom feed unavailable</strong><span>Showing the last confirmed classroom readings.</span></div>
            <span>Polling will retry automatically.</span>
          </div>
        )}

        <section id="overview" aria-labelledby="overview-title">
          <div className="section-heading">
            <div>
              <p className="eyebrow">Room pulse</p>
              <h2 id="overview-title">Live overview</h2>
            </div>
            <p>Updated {new Date(data.last_updated_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" })}</p>
          </div>
          <div className="metric-grid">
            {data.metrics.map((metric) => <MetricCard key={metric.id} metric={metric} />)}
          </div>
        </section>

        <div className="operations-grid">
          <section id="devices" aria-labelledby="device-title">
            <div className="section-heading">
              <div>
                <p className="eyebrow">Deterministic control</p>
                <h2 id="device-title">Device control</h2>
              </div>
              <p>Commands are checked before dispatch.</p>
            </div>
            <div className="device-grid">
              {data.devices.map((device) => <DeviceControlCard key={device.id} device={device} onRequest={requestControl} compact />)}
            </div>
          </section>
          <section aria-labelledby="ai-title">
            <div className="section-heading section-heading--compact">
              <div><p className="eyebrow">Anonymous tracking</p><h2 id="ai-title">AI insights</h2></div>
            </div>
            <AIInsightPanel insight={data.ai} />
          </section>
        </div>

        <section id="analytics" className="analytics-section" aria-labelledby="analytics-title">
          <div className="section-heading">
            <div><p className="eyebrow">Measured impact</p><h2 id="analytics-title">Energy analytics</h2></div>
            <p>Baseline: matched occupied periods · v1</p>
          </div>
          <EnergyCharts charts={data.charts} />
        </section>

        <footer className="dashboard-footer">
          <span>Nexergy · Classroom Energy Intelligence</span>
          <span>AI recommends. Deterministic policy decides. Devices acknowledge.</span>
        </footer>
      </main>
    </div>
  );
}
