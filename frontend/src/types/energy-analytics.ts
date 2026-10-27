import type { ISODateTime, UUID } from "./contracts";

export type AnalyticsPeriod = "day" | "week" | "month";

export interface RuntimeMetrics {
  on_seconds: number;
  off_seconds: number;
  unknown_seconds: number;
  elapsed_seconds: number;
  coverage_pct: number;
  on_pct: number;
  activations: number;
  transitions: number;
  average_on_seconds: number | null;
  longest_on_seconds: number;
}

export interface RuntimeBucket extends RuntimeMetrics {
  start: ISODateTime;
  end: ISODateTime;
  label: string;
}

export interface EnergyMetrics {
  estimated_energy_kwh: number;
  daily_24h_baseline_kwh: number;
  period_baseline_kwh: number;
  theoretical_difference_kwh: number;
  provisional_difference_pct: number | null;
  final_savings_pct: number | null;
  is_complete: boolean;
}

export interface DeviceAnalytics {
  key: "lighting" | "fan" | "air_conditioner";
  device_id: UUID;
  name: string;
  indicator: "yellow" | "blue" | "red";
  assumed_power_w: number;
  tracking_started_at: ISODateTime | null;
  first_observed_at: ISODateTime | null;
  last_observed_at: ISODateTime | null;
  source: string;
  timestamp_accuracy: string;
  runtime: RuntimeMetrics;
  energy: EnergyMetrics;
  hourly: RuntimeBucket[];
  daily: RuntimeBucket[];
  manual_observation_segments: number;
  fault_observation_segments: number;
}

export interface AnalyticsReport {
  schema_version: string;
  methodology_version: string;
  classroom_id: UUID;
  period: AnalyticsPeriod;
  timezone: string;
  period_start: ISODateTime;
  period_end: ISODateTime;
  evaluated_until: ISODateTime;
  as_of: ISODateTime;
  calendar_days: number;
  period_elapsed_seconds: number;
  tracking_started_at: ISODateTime | null;
  stale_after_seconds: number;
  is_partial: boolean;
  devices: DeviceAnalytics[];
  total_energy: EnergyMetrics;
  warnings: string[];
  limitations: string[];
}
