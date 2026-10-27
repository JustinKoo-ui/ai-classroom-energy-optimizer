import type { ISODateTime } from "./contracts";

export interface AnalystSections {
  executive_summary: string;
  environmental_analysis: string;
  device_runtime_analysis: string;
  energy_analysis: string;
  automation_observations: string;
  recommendations: string[];
  data_quality_and_limitations: string;
}

export interface AnalystResponse {
  status: "ready" | "unavailable" | "rate_limited";
  message: string;
  generated_at: ISODateTime | null;
  metrics_as_of: ISODateTime | null;
  summary_hash: string | null;
  cached: boolean;
  provider: string | null;
  model: string | null;
  sections: AnalystSections | null;
  mandatory_disclosures: string[];
  retry_after_seconds: number | null;
}
