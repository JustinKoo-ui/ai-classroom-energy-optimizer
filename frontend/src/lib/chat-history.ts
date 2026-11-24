import type { AnalyticsPeriod } from "../types/energy-analytics";
import type { ChatRequest, ChatTurn } from "../types/ai-chat";

// The backend accepts up to 6 turns of 2000 characters (backend/app/schemas/energy_chat.py).
// The browser sends less: every earlier turn is resent with each question and counts
// against the AI provider's tokens-per-minute limit, so a shorter memory leaves room
// for more questions. The byte budget stays under the Worker's 32 KiB chat limit.
export const MAX_QUESTION_CHARS = 500;
export const MAX_TURN_CHARS = 1200;
export const MAX_HISTORY_TURNS = 4;
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
  maxBytes = MAX_BODY_BYTES,
): ChatRequest {
  const history = transcript
    .map((turn) => ({ role: turn.role, content: clip(turn.content, MAX_TURN_CHARS) }))
    .filter((turn) => turn.content.length > 0)
    .slice(-MAX_HISTORY_TURNS);
  const request: ChatRequest = { message: clip(question, MAX_QUESTION_CHARS), history, date, period };
  while (request.history.length > 0 && bodyBytes(request) > maxBytes) {
    request.history = request.history.slice(1);
  }
  return request;
}
