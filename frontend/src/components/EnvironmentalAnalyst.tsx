"use client";

import { useState } from "react";
import { AnalystCommentary } from "@/src/components/AnalystCommentary";
import { useEnergyAnalytics } from "@/src/hooks/useEnergyAnalytics";
import { energyPresentation } from "@/src/lib/analytics-presentation";
import type { UUID } from "@/src/types/contracts";
import type {
  AnalyticsPeriod,
  AnalyticsReport,
  DeviceAnalytics,
  EnergyMetrics,
  RuntimeBucket,
} from "@/src/types/energy-analytics";

export function EnvironmentalAnalyst({ classroomId }: { classroomId: UUID }) {
  const [period, setPeriod] = useState<AnalyticsPeriod>("day");
  const [date, setDate] = useState("");
  const analytics = useEnergyAnalytics(
    process.env.NEXT_PUBLIC_CLASSROOM_ID ?? classroomId,
    period,
    date,
  );
  const report = analytics.report;
  const dateValue = date || (report ? calendarDate(report.as_of, report.timezone) : "");

  return (
    <section id="environmental-analyst" className="analytics-section environmental-analyst" aria-labelledby="analyst-title">
      <div className="section-heading">
        <div><p className="eyebrow">Recorded runtime · assumed power</p><h2 id="analyst-title">AI Environmental Analyst</h2></div>
        <span className="analyst-status">{report ? report.total_energy.is_complete ? "Complete runtime coverage" : Date.parse(report.as_of) < Date.parse(report.period_end) ? "Period in progress" : "Incomplete runtime coverage" : "Live history"}</span>
      </div>
      <div className="analyst-toolbar">
        <label>Reporting date<input type="date" value={dateValue} onChange={(event) => setDate(event.target.value)} /></label>
        <label>Period<select value={period} onChange={(event) => setPeriod(event.target.value as AnalyticsPeriod)}>
          <option value="day">Daily</option><option value="week">Weekly</option><option value="month">Monthly</option>
        </select></label>
        <button type="button" onClick={() => setDate("")}>Today</button>
        <button type="button" onClick={analytics.refresh}>Refresh metrics</button>
        <span>Independent analytics refresh every 30 seconds</span>
      </div>
      {analytics.loading && <p role="status">Loading recorded runtime and mathematical estimates…</p>}
      {analytics.error && <div className="inline-alert" role="alert">{analytics.error} {report ? "Showing the last successful calculation; check its calculation timestamp." : "Recorded analytics will appear when the local backend is available."}</div>}
      {report && <DeterministicReport report={report} />}
      <AnalystCommentary classroomId={process.env.NEXT_PUBLIC_CLASSROOM_ID ?? classroomId} period={period} date={date || null} currentReportingDate={dateValue} timezone={report?.timezone ?? null} metricsAvailable={Boolean(report) && !analytics.loading} />
    </section>
  );
}

function DeterministicReport({ report }: { report: AnalyticsReport }) {
  const total = report.total_energy;
  const presentation = energyPresentation(report, report.devices, total);
  const coverageLabel = presentation.coverage_pct === null
    ? "No elapsed runtime coverage"
    : `${percentage(presentation.coverage_pct)} runtime coverage`;
  return (
    <div aria-label="Calculated numerical analytics">
      <div className="connection-banner analyst-disclosure" role="status">
        <div>
          <strong>Recorded energy estimates · {coverageLabel}</strong>
          <span>Assumed watts × recorded ON hours. UNKNOWN time is excluded from the estimate, so incomplete coverage can understate usage.</span>
          <span>Period: {timestamp(report.period_start, report.timezone)} to {timestamp(report.period_end, report.timezone)} · {report.timezone}</span>
          <span>Evaluated through {timestamp(report.evaluated_until, report.timezone)} · calculated {timestamp(report.as_of, report.timezone)}</span>
        </div>
      </div>
      {presentation.reasons.length > 0 && <div className="analyst-warnings" role="status"><strong>Data completeness</strong><ul>{presentation.reasons.map((reason) => <li key={reason}>{reason}.</li>)}</ul><p>Percentages are withheld until the period ends with complete runtime coverage. Earlier gaps remain UNKNOWN after the period ends.</p></div>}
      <div className="analyst-summary-grid">
        <Summary label="Recorded ON energy estimate" value={`${number(total.estimated_energy_kwh)} kWh`} coverage={coverageLabel} detail="Estimated from recorded intervals and assumed watts; electricity is not measured." />
        <Summary label="Theoretical 24-hour baseline" value={`${number(total.daily_24h_baseline_kwh)} kWh/day`} detail="All three represented appliances ON continuously for 24 hours." />
        <Summary label="Theoretical period baseline" value={`${number(total.period_baseline_kwh)} kWh`} detail={`${report.calendar_days} calendar day(s) × fixed 24-hour baseline.`} />
        <Summary label="Baseline minus recorded estimate" value={`${signed(total.theoretical_difference_kwh)} kWh`} detail={total.is_complete ? "A theoretical comparison using assumed watts." : "Full-period reference difference. Missing and future usage cannot establish savings."} />
      </div>
      <div className="analyst-calculation-note">
        <strong>{presentation.final_pct === null ? `Final theoretical comparison: ${presentation.reasons.join(" · ") || "No comparable baseline"}.` : `Final theoretical difference: ${percentage(presentation.final_pct)}`}</strong>
        <span>{presentation.provisional_pct === null ? "A percentage comparison requires a finished period, complete runtime coverage, and a positive baseline." : `Period baseline difference: ${percentage(presentation.provisional_pct)}.`} These estimates do not establish measured or verified energy savings.</span>
      </div>
      <div className="analyst-device-grid">
        {report.devices.map((device) => <DeviceRuntime key={device.device_id} device={device} timezone={report.timezone} />)}
      </div>
      {report.devices.length === 0 && <p role="status">No represented devices are registered. No runtime or energy history can be inferred.</p>}
      <div className="analyst-table-scroll">
        <table className="analyst-table">
          <caption>Estimated energy by device · configured prototype assumptions</caption>
          <thead><tr><th scope="col">Device</th><th scope="col">Assumed W</th><th scope="col">Recorded energy kWh / coverage</th><th scope="col">24h baseline kWh</th><th scope="col">Period baseline kWh</th><th scope="col">Difference kWh</th><th scope="col">Provisional difference %</th><th scope="col">Final theoretical %</th></tr></thead>
          <tbody>{report.devices.map((device) => <tr key={device.device_id}><th scope="row">{deviceName(device)}</th><td>{number(device.assumed_power_w, 0)}</td><EnergyCells report={report} devices={[device]} energy={device.energy} /></tr>)}</tbody>
          <tfoot><tr><th scope="row">Combined</th><td>{number(report.devices.reduce((sum, device) => sum + device.assumed_power_w, 0), 0)}</td><EnergyCells report={report} devices={report.devices} energy={total} /></tr></tfoot>
        </table>
      </div>
      <p className="analyst-method-note">Coverage describes recorded ON/OFF time within the elapsed reporting period. Energy and baseline values use assumed watts. See Methodology and limitations for details.</p>
      <div className="analyst-chart-grid">
        {report.devices.map((device) => <RuntimeChart key={device.device_id} device={device} hourly={report.period === "day"} />)}
      </div>
      {report.period === "day" && <details className="analyst-details"><summary>Daily runtime totals for this reporting day</summary><DailyRuntimeTable devices={report.devices} /></details>}
      <details className="analyst-details analyst-methodology"><summary>Methodology and limitations</summary>
        <p>Energy = assumed watts × recorded ON seconds ÷ 3,600 ÷ 1,000. Combined coverage is weighted by elapsed device-seconds. Missing time is never treated as OFF.</p>
        <p>Tracking began {timestamp(report.tracking_started_at, report.timezone)}. Observations expire after {report.stale_after_seconds} seconds; earlier time and stale gaps remain UNKNOWN.</p>
        <p>Illustrative power assumptions are configured in the backend and are not calibrated equipment specifications. The reporting calendar uses {report.timezone}; weeks begin on Monday.</p>
        <ul>{report.limitations.map((limitation) => <li key={limitation}>{limitation}</li>)}</ul>
        {report.warnings.length > 0 && <><h4>Calculation diagnostics</h4><ul>{report.warnings.map((warning) => <li key={warning}>{warning}</li>)}</ul></>}
        <p>Method: {report.methodology_version}. History starts at reliable recorded observations; earlier days are not reconstructed from demo data.</p>
      </details>
    </div>
  );
}

function Summary({ label, value, detail, coverage }: { label: string; value: string; detail: string; coverage?: string }) {
  return <article className="metric-card analyst-summary"><p className="metric-card__label">{label}</p><strong>{value}</strong>{coverage && <span className="analyst-energy-coverage">{coverage}</span>}<p className="metric-card__detail">{detail}</p></article>;
}

function DeviceRuntime({ device, timezone }: { device: DeviceAnalytics; timezone: string }) {
  const runtime = device.runtime;
  return (
    <article className={`chart-card analyst-device analyst-device--${device.indicator}`}>
      <h3><i aria-hidden="true" />{deviceName(device)}</h3>
      <p className="analyst-indicator">{device.indicator} indicator · {number(device.assumed_power_w, 0)} W assumed</p>
      <strong className="analyst-runtime">{duration(runtime.on_seconds)} <small>observed ON</small></strong>
      {runtime.coverage_pct === 0 && <p className="analyst-no-coverage">No ON/OFF observation coverage. Zero recorded ON time does not establish zero usage.</p>}
      <dl className="analyst-facts">
        <div><dt>OFF</dt><dd>{duration(runtime.off_seconds)}</dd></div><div><dt>UNKNOWN</dt><dd>{duration(runtime.unknown_seconds)}</dd></div>
        <div><dt>Activations</dt><dd>{runtime.activations}</dd></div><div><dt>ON/OFF transitions</dt><dd>{runtime.transitions}</dd></div>
        <div><dt>Average ON run</dt><dd>{runtime.average_on_seconds === null ? "No recorded ON runs" : duration(runtime.average_on_seconds)}</dd></div><div><dt>Longest ON run</dt><dd>{duration(runtime.longest_on_seconds)}</dd></div>
        <div><dt>Elapsed period ON</dt><dd>{percentage(runtime.on_pct)}</dd></div><div><dt>Observation coverage</dt><dd>{percentage(runtime.coverage_pct)}</dd></div>
        <div><dt>Elapsed period</dt><dd>{duration(runtime.elapsed_seconds)}</dd></div><div><dt>Manual / fault segments</dt><dd>{device.manual_observation_segments} / {device.fault_observation_segments}</dd></div>
      </dl>
      <details className="analyst-device-source"><summary>State source and tracking bounds</summary><p>Source: {device.source}. {device.timestamp_accuracy}</p><p>Tracking start: {timestamp(device.tracking_started_at, timezone)}</p><p>First observation: {timestamp(device.first_observed_at, timezone)}</p><p>Last observation: {timestamp(device.last_observed_at, timezone)}</p><p>Compressed unchanged-state observation bounds may extend beyond this reporting period. Runtime calculations are clipped to the reporting bounds.</p></details>
    </article>
  );
}

function EnergyCells({ report, devices, energy }: { report: AnalyticsReport; devices: DeviceAnalytics[]; energy: EnergyMetrics }) {
  const presentation = energyPresentation(report, devices, energy);
  const reason = presentation.reasons.join(" · ") || "No comparable baseline";
  const provisionalReason = presentation.reasons.includes("Incomplete runtime coverage")
    ? "Incomplete runtime coverage"
    : presentation.reasons[0] || "No comparable baseline";
  return <>
    <td><span>{number(energy.estimated_energy_kwh)}</span><small className="analyst-energy-coverage">{presentation.coverage_pct === null ? "No elapsed runtime" : `${percentage(presentation.coverage_pct)} coverage`}</small></td>
    <td>{number(energy.daily_24h_baseline_kwh)}</td><td>{number(energy.period_baseline_kwh)}</td><td>{signed(energy.theoretical_difference_kwh)}</td>
    <td>{presentation.provisional_pct === null ? <span className="analyst-comparison-reason">Withheld · {provisionalReason}</span> : percentage(presentation.provisional_pct)}</td>
    <td>{presentation.final_pct === null ? <span className="analyst-comparison-reason">{reason}</span> : percentage(presentation.final_pct)}</td>
  </>;
}

function RuntimeChart({ device, hourly }: { device: DeviceAnalytics; hourly: boolean }) {
  const buckets = hourly ? device.hourly : device.daily;
  return <article className={`chart-card analyst-runtime-chart analyst-device--${device.indicator}`}>
    <header className="chart-card__header"><div><p className="eyebrow">{hourly ? "Hourly" : "Daily"} runtime history</p><h3>{deviceName(device)}</h3></div></header>
    <p className="analyst-legend"><span><i className="analyst-on" />ON</span><span><i className="analyst-off" />OFF</span><span><i className="analyst-unknown" />UNKNOWN</span></p>
    <div className="analyst-bucket-list">{buckets.map((bucket) => <RuntimeBar key={bucket.start} bucket={bucket} hourly={hourly} />)}</div>
    <details className="analyst-details"><summary>Exact calculated durations</summary><div className="analyst-table-scroll"><table className="analyst-table"><thead><tr><th scope="col">Period</th><th scope="col">ON</th><th scope="col">OFF</th><th scope="col">UNKNOWN</th><th scope="col">Coverage</th></tr></thead><tbody>{buckets.map((bucket) => <tr key={bucket.start}><th scope="row">{bucket.label}</th><td>{duration(bucket.on_seconds)}</td><td>{duration(bucket.off_seconds)}</td><td>{duration(bucket.unknown_seconds)}</td><td>{percentage(bucket.coverage_pct)}</td></tr>)}</tbody></table></div></details>
  </article>;
}

function RuntimeBar({ bucket, hourly }: { bucket: RuntimeBucket; hourly: boolean }) {
  const elapsed = bucket.elapsed_seconds;
  const width = (seconds: number) => elapsed > 0 ? `${(seconds / elapsed) * 100}%` : "0%";
  const label = `${bucket.label}: ON ${duration(bucket.on_seconds)}, OFF ${duration(bucket.off_seconds)}, UNKNOWN ${duration(bucket.unknown_seconds)}, coverage ${percentage(bucket.coverage_pct)}`;
  const parts = bucket.label.match(/^\d{4}-(\d{2}-\d{2})T(\d{2}:\d{2}).*([+-]\d{2}:\d{2}|Z)$/);
  const compactLabel = parts ? hourly ? `${parts[2]} ${parts[3]}` : parts[1] : bucket.label;
  return <div className={`analyst-bucket${hourly ? " analyst-bucket--hourly" : ""}`}><span title={bucket.label}>{compactLabel}</span><div className="analyst-bucket-track" role="img" aria-label={label} title={label}><i className="analyst-on" style={{ width: width(bucket.on_seconds) }} /><i className="analyst-off" style={{ width: width(bucket.off_seconds) }} /><i className="analyst-unknown" style={{ width: width(bucket.unknown_seconds) }} /></div><small>{duration(bucket.on_seconds)}</small></div>;
}

function DailyRuntimeTable({ devices }: { devices: DeviceAnalytics[] }) {
  return <div className="analyst-table-scroll"><table className="analyst-table"><thead><tr><th scope="col">Day</th><th scope="col">Device</th><th scope="col">ON</th><th scope="col">OFF</th><th scope="col">UNKNOWN</th><th scope="col">Coverage</th></tr></thead><tbody>{devices.flatMap((device) => device.daily.map((bucket) => <tr key={`${device.device_id}:${bucket.start}`}><th scope="row">{bucket.label}</th><td>{deviceName(device)}</td><td>{duration(bucket.on_seconds)}</td><td>{duration(bucket.off_seconds)}</td><td>{duration(bucket.unknown_seconds)}</td><td>{percentage(bucket.coverage_pct)}</td></tr>))}</tbody></table></div>;
}

function deviceName(device: DeviceAnalytics): string {
  return { lighting: "Light bulb", fan: "Fan", air_conditioner: "Air conditioner" }[device.key];
}

function duration(seconds: number): string {
  const rounded = Math.max(0, Math.round(seconds));
  const hours = Math.floor(rounded / 3600);
  const minutes = Math.floor((rounded % 3600) / 60);
  const remainder = rounded % 60;
  return hours > 0 ? `${hours}h ${minutes}m ${remainder}s` : minutes > 0 ? `${minutes}m ${remainder}s` : `${remainder}s`;
}

function number(value: number, digits = 3): string {
  return value.toLocaleString(undefined, { minimumFractionDigits: digits, maximumFractionDigits: digits });
}

function signed(value: number): string {
  return `${value > 0 ? "+" : ""}${number(value)}`;
}

function percentage(value: number | null): string {
  return value === null ? "Unavailable" : `${number(value, 1)}%`;
}

function timestamp(value: string | null, timezone: string): string {
  return value ? new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "medium", timeZone: timezone }).format(new Date(value)) : "Not recorded";
}

function calendarDate(value: string, timezone: string): string {
  const parts = new Intl.DateTimeFormat("en-CA", { year: "numeric", month: "2-digit", day: "2-digit", timeZone: timezone }).formatToParts(new Date(value));
  const part = (type: string) => parts.find((item) => item.type === type)?.value ?? "";
  return `${part("year")}-${part("month")}-${part("day")}`;
}
