import type {
  AnalyticsReport,
  DeviceAnalytics,
  EnergyMetrics,
} from "../types/energy-analytics";

const UNKNOWN_TOLERANCE_SECONDS = 0.001;

export function runtimeCoverage(devices: DeviceAnalytics[]) {
  const elapsed = devices.reduce((sum, device) => sum + device.runtime.elapsed_seconds, 0);
  const known = devices.reduce(
    (sum, device) => sum + device.runtime.on_seconds + device.runtime.off_seconds,
    0,
  );
  const unknown = devices.reduce((sum, device) => sum + device.runtime.unknown_seconds, 0);
  return {
    elapsed_seconds: elapsed,
    known_seconds: known,
    unknown_seconds: unknown,
    // Combined coverage uses device-seconds, never a sum of percentages or watts.
    coverage_pct: elapsed > 0 ? Math.min(100, Math.max(0, known / elapsed * 100)) : null,
  };
}

export function energyPresentation(
  report: Pick<AnalyticsReport, "period_start" | "period_end" | "as_of">,
  devices: DeviceAnalytics[],
  energy: EnergyMetrics,
) {
  const coverage = runtimeCoverage(devices);
  const reasons: string[] = [];
  const start = Date.parse(report.period_start);
  const end = Date.parse(report.period_end);
  const asOf = Date.parse(report.as_of);

  if (asOf < start) reasons.push("Reporting period has not started");
  else if (asOf < end) reasons.push("Reporting period still in progress");

  if (devices.length === 0 || coverage.known_seconds <= 0) {
    reasons.push("No runtime observations recorded");
  } else if (devices.some((device) => device.runtime.unknown_seconds > UNKNOWN_TOLERANCE_SECONDS)) {
    if (devices.some((device) => device.tracking_started_at !== null
      && Date.parse(device.tracking_started_at) > start
      && Date.parse(device.tracking_started_at) <= asOf)) {
      reasons.push("Tracking started partway through the period");
    }
    reasons.push("Incomplete runtime coverage");
  }

  if (energy.period_baseline_kwh <= 0) reasons.push("No positive baseline configured");
  if (!energy.is_complete && reasons.length === 0) reasons.push("Incomplete runtime coverage");

  const canCompare = energy.is_complete && reasons.length === 0;
  return {
    ...coverage,
    reasons,
    provisional_pct: canCompare ? energy.provisional_difference_pct : null,
    final_pct: canCompare ? energy.final_savings_pct : null,
  };
}
