"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { ChatAuthError, sendChatMessage } from "@/src/api/ai-chat-service";
import { buildChatRequest } from "@/src/lib/chat-history";
import type { ChatRequest, ChatResponse, ChatRole } from "@/src/types/ai-chat";
import type { UUID } from "@/src/types/contracts";
import type { AnalyticsPeriod } from "@/src/types/energy-analytics";

export interface ChatMessage {
  id: number;
  role: ChatRole;
  content: string;
  reply?: ChatResponse;
}

/** Waits longer than this are shown as an error instead of retried automatically. */
const MAX_AUTO_WAIT_SECONDS = 45;

function sleep(ms: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve) => {
    const timer = window.setTimeout(resolve, ms);
    signal.addEventListener("abort", () => { window.clearTimeout(timer); resolve(); }, { once: true });
  });
}

/** Conversation state lives in memory only; nothing is written to browser storage. */
export function useAIChat(classroomId: UUID) {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  // Seconds left before an automatic retry after the AI provider's per-minute limit.
  const [waitSeconds, setWaitSeconds] = useState<number | null>(null);
  const [authRequired, setAuthRequired] = useState(false);
  const controllerRef = useRef<AbortController | null>(null);
  const nextId = useRef(1);

  useEffect(() => () => controllerRef.current?.abort(), []);

  /** Resolves true when an answer was added; false leaves the question unsent. */
  const send = useCallback(async (question: string, accessToken: string, period: AnalyticsPeriod) => {
    const text = question.trim();
    if (!text || loading) return false;
    controllerRef.current?.abort();
    const controller = new AbortController();
    controllerRef.current = controller;
    const request: ChatRequest = buildChatRequest(text, messages, period);
    const userMessage: ChatMessage = { id: nextId.current++, role: "user", content: request.message };
    setMessages((current) => [...current, userMessage]);
    setLoading(true);
    setError(null);
    try {
      let reply = await sendChatMessage(classroomId, request, accessToken, controller.signal);
      // Busy or per-minute limit: wait it out once and ask again, instead of
      // making the person resend.
      const wait = reply.retry_after_seconds;
      if (reply.status === "rate_limited" && wait !== null && wait <= MAX_AUTO_WAIT_SECONDS) {
        for (let remaining = wait + 1; remaining > 0 && !controller.signal.aborted; remaining--) {
          setWaitSeconds(remaining);
          await sleep(1000, controller.signal);
        }
        setWaitSeconds(null);
        if (controller.signal.aborted) return false;
        reply = await sendChatMessage(classroomId, request, accessToken, controller.signal);
      }
      if (controller.signal.aborted) return false;
      if (reply.status !== "ready" || !reply.answer) {
        throw new Error(
          reply.message + (reply.retry_after_seconds !== null ? ` Try again in ${reply.retry_after_seconds} seconds.` : ""),
        );
      }
      setAuthRequired(false);
      setMessages((current) => [
        ...current,
        { id: nextId.current++, role: "assistant", content: reply.answer ?? "", reply },
      ]);
      return true;
    } catch (cause) {
      if (controller.signal.aborted) return false;
      if (cause instanceof ChatAuthError) setAuthRequired(true);
      // Unanswered questions are removed so they are not sent back as history.
      setMessages((current) => current.filter((message) => message.id !== userMessage.id));
      setError(cause instanceof Error ? cause.message : "The AI assistant is unavailable.");
      return false;
    } finally {
      if (controllerRef.current === controller) {
        controllerRef.current = null;
        setLoading(false);
        setWaitSeconds(null);
      }
    }
  }, [classroomId, loading, messages]);

  const clear = useCallback(() => {
    controllerRef.current?.abort();
    controllerRef.current = null;
    setMessages([]);
    setError(null);
    setLoading(false);
    setWaitSeconds(null);
  }, []);

  return { messages, error, loading, waitSeconds, authRequired, send, clear };
}
