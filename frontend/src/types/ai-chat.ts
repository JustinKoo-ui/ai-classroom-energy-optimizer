import type { ISODateTime } from "./contracts";
import type { AnalyticsPeriod } from "./energy-analytics";

/** Mirrors backend/app/schemas/energy_chat.py. Update both in the same change. */
export type ChatRole = "user" | "assistant";

export interface ChatTurn {
  role: ChatRole;
  content: string;
}

export interface ChatRequest {
  message: string;
  history: ChatTurn[];
  date: string | null;
  period: AnalyticsPeriod;
}

export interface ChatResponse {
  status: "ready" | "unavailable" | "rate_limited";
  message: string;
  answer: string | null;
  on_topic: boolean | null;
  suggested_questions: string[];
  generated_at: ISODateTime | null;
  metrics_as_of: ISODateTime | null;
  period: AnalyticsPeriod | null;
  data_complete: boolean | null;
  model: string | null;
  retry_after_seconds: number | null;
}
