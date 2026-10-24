import type { DashboardMetric } from "@/src/types/dashboard";

const metricCodes: Record<DashboardMetric["id"], string> = {
  occupancy: "OC",
  temperature: "TE",
  humidity: "HU",
  ambient_light: "LX",
  current_power: "PW",
  energy_today: "EN",
  energy_saved: "SV",
};

export function MetricCard({ metric }: { metric: DashboardMetric }) {
  return (
    <article className={`metric-card metric-card--${metric.id}`}>
      <div className="metric-card__topline">
        <span className="metric-card__code" aria-hidden="true">
          {metricCodes[metric.id]}
        </span>
        <span className={`status-dot status-dot--${metric.status}`}>
          {metric.status === "ok"
            ? "Live"
            : metric.status === "unknown"
              ? "Unknown"
              : metric.status}
        </span>
      </div>
      <p className="metric-card__label">{metric.label}</p>
      {metric.status === "error" ? (
        <div className="metric-card__error">
          <strong>{metric.id === "occupancy" ? "Camera feed unavailable" : "Sensor unavailable"}</strong>
          <span>{metric.detail}</span>
        </div>
      ) : (
        <>
          <p className="metric-card__value">
            {metric.display_value}
            <span>{metric.unit}</span>
          </p>
          <p className="metric-card__detail">{metric.detail}</p>
          <p className={`metric-card__trend metric-card__trend--${metric.trend}`}>
            {metric.trend_label}
          </p>
        </>
      )}
    </article>
  );
}
