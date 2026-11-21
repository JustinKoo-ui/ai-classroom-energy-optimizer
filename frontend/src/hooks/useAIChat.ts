"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { sendChatMessage } from "@/src/api/ai-chat-service";
import { buildChatRequest } from "@/src/lib/chat-history";
import type { ChatResponse, ChatRole } from "@/src/types/ai-chat";
import type { UUID } from "@/src/types/contracts";
import type { AnalyticsPeriod } from "@/src/types/energy-analytics";

export interface ChatMessage {
  id: number;
  role: ChatRole;
  content: string;
  reply?: ChatResponse;
}

/** Conversation state lives in memory only; nothing is written to browser storage. */
export function useAIChat(classroomId: UUID) {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
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
    const request = buildChatRequest(text, messages, period);
    const userMessage: ChatMessage = { id: nextId.current++, role: "user", content: request.message };
    setMessages((current) => [...current, userMessage]);
    setLoading(true);
    setError(null);
    try {
      const reply = await sendChatMessage(classroomId, request, accessToken, controller.signal);
      if (controller.signal.aborted) return false;
      if (reply.status !== "ready" || !reply.answer) {
        throw new Error(
          reply.message + (reply.retry_after_seconds !== null ? ` Try again in ${reply.retry_after_seconds} seconds.` : ""),
        );
      }
      setMessages((current) => [
        ...current,
        { id: nextId.current++, role: "assistant", content: reply.answer ?? "", reply },
      ]);
      return true;
    } catch (cause) {
      if (controller.signal.aborted) return false;
      // Unanswered questions are removed so they are not sent back as history.
      setMessages((current) => current.filter((message) => message.id !== userMessage.id));
      setError(cause instanceof Error ? cause.message : "The AI assistant is unavailable.");
      return false;
    } finally {
      if (controllerRef.current === controller) {
        controllerRef.current = null;
        setLoading(false);
      }
    }
  }, [classroomId, loading, messages]);

  const clear = useCallback(() => {
    controllerRef.current?.abort();
    controllerRef.current = null;
    setMessages([]);
    setError(null);
    setLoading(false);
  }, []);

  return { messages, error, loading, send, clear };
}
