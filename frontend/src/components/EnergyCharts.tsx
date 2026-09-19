import type {
  ApplianceEnergyDatum,
  DashboardCharts,
  TimeSeriesPoint,
} from "@/src/types/dashboard";

export function EnergyCharts({ charts }: { charts: DashboardCharts }) {
  return (
    <div className="chart-grid">
      <PowerChart points={charts.power_over_time} />
      <EnergyMixChart items={charts.energy_by_appliance} />
      <ComparisonChart points={charts.baseline_vs_ai} />
      <SavingsChart points={charts.daily_savings} />
    </div>
  );
}

function ChartHeader({ eyebrow, title, meta }: { eyebrow: string; title: string; meta: string }) {
  return (
    <header className="chart-card__header">
      <div><p className="eyebrow">{eyebrow}</p><h3>{title}</h3></div>
      <span>{meta}</span>
    </header>
  );
}

function PowerChart({ points }: { points: TimeSeriesPoint[] }) {
  const max = Math.max(...points.map((point) => point.value), 1) * 1.15;
  const polygon = [
    "0% 100%",
    ...points.map((point, index) => {
      const x = points.length === 1 ? 0 : (index / (points.length - 1)) * 100;
      const y = 100 - (point.value / max) * 100;
      return `${x}% ${y}%`;
    }),
    "100% 100%",
  ].join(", ");
  const latest = points.at(-1)?.value ?? 0;
  return (
    <article className="chart-card chart-card--wide">
      <ChartHeader eyebrow="Live demand" title="Power over time" meta={`${latest.toFixed(2)} kW now`} />
      <div className="power-chart" aria-label="Power demand over time">
        <div className="power-chart__grid"><i /><i /><i /><i /></div>
        <div className="power-chart__area" style={{ clipPath: `polygon(${polygon})` }} />
        {points.map((point, index) => {
          const x = points.length === 1 ? 0 : (index / (points.length - 1)) * 100;
          const y = 100 - (point.value / max) * 100;
          return <i className="power-chart__point" key={point.label} style={{ left: `${x}%`, top: `${y}%` }} title={`${point.label}: ${point.value} kW`} />;
        })}
      </div>
      <div className="axis-labels">{points.map((point) => <span key={point.label}>{point.label}</span>)}</div>
    </article>
  );
}

function EnergyMixChart({ items }: { items: ApplianceEnergyDatum[] }) {
  const total = items.reduce((sum, item) => sum + item.value_kwh, 0);
  let cursor = 0;
  const segments = items.map((item) => {
    const start = (cursor / total) * 100;
    cursor += item.value_kwh;
    const end = (cursor / total) * 100;
    return `${item.color} ${start}% ${end}%`;
  });
  return (
    <article className="chart-card">
      <ChartHeader eyebrow="Consumption mix" title="Energy by appliance" meta="Today" />
      <div className="donut-layout">
        <div className="donut-chart" style={{ background: `conic-gradient(${segments.join(",")})` }}>
          <div><strong>{total.toFixed(1)}</strong><span>kWh total</span></div>
        </div>
        <div className="donut-legend">
          {items.map((item) => (
            <div key={item.label}><i style={{ background: item.color }} /><span>{item.label}</span><strong>{item.value_kwh.toFixed(1)} kWh</strong></div>
          ))}
        </div>
      </div>
    </article>
  );
}

function ComparisonChart({ points }: { points: TimeSeriesPoint[] }) {
  const max = Math.max(...points.flatMap((point) => [point.value, point.secondary_value ?? 0]), 1);
  return (
    <article className="chart-card chart-card--wide">
      <ChartHeader eyebrow="Optimization proof" title="Baseline vs AI consumption" meta="Last 5 days" />
      <div className="comparison-legend"><span><i className="baseline" />Baseline</span><span><i className="optimized" />AI optimized</span></div>
      <div className="comparison-chart">
        {points.map((point) => (
          <div className="comparison-group" key={point.label}>
            <div className="comparison-bars">
              <i className="baseline" style={{ height: `${(point.value / max) * 100}%` }} title={`Baseline ${point.value} kWh`} />
              <i className="optimized" style={{ height: `${((point.secondary_value ?? 0) / max) * 100}%` }} title={`AI ${point.secondary_value} kWh`} />
            </div>
            <span>{point.label}</span>
          </div>
        ))}
      </div>
    </article>
  );
}

function SavingsChart({ points }: { points: TimeSeriesPoint[] }) {
  const max = Math.max(...points.map((point) => point.value), 1);
  const total = points.reduce((sum, point) => sum + point.value, 0);
  return (
    <article className="chart-card chart-card--savings">
      <ChartHeader eyebrow="Avoided energy" title="Daily energy savings" meta={`${total.toFixed(1)} kWh week`} />
      <div className="savings-bars">
        {points.map((point) => (
          <div key={point.label}>
            <strong>{point.value.toFixed(1)}</strong>
            <i style={{ height: `${(point.value / max) * 100}%` }} />
            <span>{point.label}</span>
          </div>
        ))}
      </div>
    </article>
  );
}

