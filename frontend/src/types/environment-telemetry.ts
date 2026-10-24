import type { Freshness, ISODateTime, UUID } from "./contracts";

export interface LatestEnvironmentTelemetry {
  classroom_id: UUID;
  temperature_c: number | null;
  relative_humidity_pct: number | null;
  illuminance_lux: number | null;
  illuminance_adc: number | null;
  motion_detected: boolean | null;
  sample_interval_ms: number | null;
  observed_at: ISODateTime | null;
  freshness: Freshness;
}
