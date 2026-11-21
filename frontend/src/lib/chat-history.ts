import type { AnalyticsPeriod } from "../types/energy-analytics";
import type { ChatRequest, ChatTurn } from "../types/ai-chat";

// Limits match backend/app/schemas/energy_chat.py. The byte budget stays under the
// Cloudflare Worker's 32 KiB chat body limit with room for JSON overhead.
export const MAX_QUESTION_CHARS = 500;
export const MAX_TURN_CHARS = 2000;
export const MAX_HISTORY_TURNS = 6;
export const MAX_BODY_BYTES = 30_000;

/** Truncate by code point so a multi-unit character (e.g. an emoji) is never split. */
function clip(text: string, limit: number): string {
  const points = Array.from(text.trim());
  return points.length <= limit ? points.join("") : points.slice(0, limit).join("");
}

function bodyBytes(request: ChatRequest): number {
  return new TextEncoder().encode(JSON.stringify(request)).byteLength;
}

/**
 * Build a request the backend and proxy will accept: the newest turns first, each
 * clipped, and the oldest dropped until the encoded body fits the byte budget.
 */
export function buildChatRequest(
  question: string,
  transcript: readonly ChatTurn[],
  period: AnalyticsPeriod,
  date: string | null = null,
): ChatRequest {
  const history = transcript
    .map((turn) => ({ role: turn.role, content: clip(turn.content, MAX_TURN_CHARS) }))
    .filter((turn) => turn.content.length > 0)
    .slice(-MAX_HISTORY_TURNS);
  const request: ChatRequest = { message: clip(question, MAX_QUESTION_CHARS), history, date, period };
  while (request.history.length > 0 && bodyBytes(request) > MAX_BODY_BYTES) {
    request.history = request.history.slice(1);
  }
  return request;
}
