import type { UUID } from "@/src/types/contracts";
import type { ChatRequest, ChatResponse } from "@/src/types/ai-chat";

/** The backend requires the report access token for the chat (ANALYST_CHAT_OPEN is off). */
export class ChatAuthError extends Error {}

export async function sendChatMessage(
  classroomId: UUID,
  body: ChatRequest,
  accessToken: string,
  signal: AbortSignal,
): Promise<ChatResponse> {
  const baseUrl = process.env.NEXT_PUBLIC_API_BASE_URL ?? "";
  const response = await fetch(
    `${baseUrl}/api/v1/classrooms/${classroomId}/analytics/chat`,
    {
      method: "POST",
      credentials: "include",
      cache: "no-store",
      signal: AbortSignal.any([signal, AbortSignal.timeout(90_000)]),
      headers: {
        "Content-Type": "application/json",
        // Sent only when typed: a backend with ANALYST_CHAT_OPEN=true needs none.
        ...(accessToken.trim() ? { Authorization: `Bearer ${accessToken.trim()}` } : {}),
      },
      body: JSON.stringify(body),
    },
  );
  if (!response.ok) {
    const detail = (await response.json().catch(() => null)) as { detail?: unknown } | null;
    if (response.status === 401) {
      throw new ChatAuthError(
        accessToken.trim()
          ? "The access token was not accepted. Use ANALYST_ACCESS_TOKEN from the backend."
          : "This backend requires an access token for the AI assistant. Enter it above.",
      );
    }
    if (response.status === 413 || response.status === 422) {
      throw new Error("That message could not be sent. Try a shorter question or clear the chat.");
    }
    throw new Error(
      typeof detail?.detail === "string"
        ? detail.detail
        : `The AI assistant is unavailable (${response.status}). Dashboard data is unaffected.`,
    );
  }
  return (await response.json()) as ChatResponse;
}
