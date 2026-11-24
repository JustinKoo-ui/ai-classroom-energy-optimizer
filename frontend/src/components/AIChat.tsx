"use client";

import { useEffect, useRef, useState } from "react";
import type { FormEvent, KeyboardEvent } from "react";
import { useAIChat } from "@/src/hooks/useAIChat";
import { MAX_QUESTION_CHARS } from "@/src/lib/chat-history";
import type { UUID } from "@/src/types/contracts";
import type { AnalyticsPeriod } from "@/src/types/energy-analytics";

const PERIOD_LABELS: Record<AnalyticsPeriod, string> = {
  day: "Today",
  week: "This week",
  month: "This month",
};

const STARTER_QUESTIONS = [
  "Summarise my room's energy use. What do the numbers mean?",
  "Which appliance was on the longest, and is that normal?",
  "How can we use the air conditioner more efficiently?",
];

/**
 * Floating AI assistant: a round button at the bottom-right of every page that
 * opens a chat panel. It stays mounted while closed, so the conversation
 * survives both closing the panel and switching pages.
 *
 * The access token field only appears when the backend asks for one
 * (ANALYST_CHAT_OPEN is off). With ANALYST_CHAT_OPEN=true anyone who can open the
 * dashboard can ask questions without it.
 */
export function AIChat({ classroomId }: { classroomId: UUID }) {
  // The access token stays in React memory only. It is not the provider API key
  // and is never written to browser storage.
  const [accessToken, setAccessToken] = useState("");
  const [period, setPeriod] = useState<AnalyticsPeriod>("day");
  const [draft, setDraft] = useState("");
  const [open, setOpen] = useState(false);
  const [seenCount, setSeenCount] = useState(0);
  const { messages, error, loading, waitSeconds, authRequired, send, clear } = useAIChat(classroomId);
  const logRef = useRef<HTMLDivElement>(null);
  const tokenRef = useRef<HTMLInputElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const bubbleRef = useRef<HTMLButtonElement>(null);
  const showToken = authRequired || accessToken !== "";
  const tokenMissing = authRequired && !accessToken.trim();
  const lastReply = messages.at(-1)?.reply;
  const unread = !open && messages.length > seenCount;

  // A new answer opens at its first line so it can be read top to bottom; while
  // waiting or after an error, show the bottom of the conversation instead.
  const anchoredAnswer = useRef<number | null>(null);
  useEffect(() => {
    const log = logRef.current;
    if (!log || !open) return;
    const last = messages.at(-1);
    if (error || loading || last?.role !== "assistant") {
      log.scrollTop = log.scrollHeight;
      return;
    }
    if (anchoredAnswer.current === last.id) return;
    anchoredAnswer.current = last.id;
    const answer = log.querySelector<HTMLElement>(`[data-message-id="${last.id}"]`);
    if (answer) {
      // Measured against the log itself, so it does not depend on CSS positioning.
      const offset = answer.getBoundingClientRect().top - log.getBoundingClientRect().top;
      log.scrollTop = Math.max(0, log.scrollTop + offset - 8);
    }
  }, [messages, loading, waitSeconds, error, open]);

  useEffect(() => {
    if (!open) return;
    // Focus the first thing the person needs: the token if required, else the question box.
    (tokenRef.current && !tokenRef.current.value.trim() ? tokenRef.current : inputRef.current)?.focus();
  }, [open, authRequired]);

  function setPanel(next: boolean) {
    setOpen(next);
    setSeenCount(messages.length);
    if (!next) bubbleRef.current?.focus();
  }

  // Escape closes the panel from anywhere while it is open.
  const messageCount = messages.length;
  useEffect(() => {
    if (!open) return;
    function onKeyDown(event: globalThis.KeyboardEvent) {
      if (event.key !== "Escape") return;
      setOpen(false);
      setSeenCount(messageCount);
      bubbleRef.current?.focus();
    }
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [open, messageCount]);

  async function ask(question: string) {
    if (tokenMissing || loading || !question.trim()) return;
    setDraft("");
    if (!(await send(question, accessToken, period))) setDraft(question);
  }

  function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    void ask(draft);
  }

  function onComposerKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
      event.preventDefault();
      void ask(draft);
    }
  }

  return (
    <>
      <div
        id="ai-chat-panel"
        className="ai-chat-panel"
        role="dialog"
        aria-modal="false"
        aria-labelledby="ai-chat-title"
        hidden={!open}
      >
        <header className="ai-chat-panel__head">
          <div>
            <p className="eyebrow">Ask about your room · read-only</p>
            <h2 id="ai-chat-title">AI energy assistant</h2>
          </div>
          <button type="button" className="ai-chat-panel__close" onClick={() => setPanel(false)} aria-label="Close AI energy assistant">
            <svg viewBox="0 0 24 24" width="20" height="20" aria-hidden="true"><path d="M6 6l12 12M18 6L6 18" stroke="currentColor" strokeWidth="2" strokeLinecap="round" /></svg>
          </button>
        </header>

        <div className="ai-chat__setup">
          <label>Data
            <select value={period} onChange={(event) => setPeriod(event.target.value as AnalyticsPeriod)}>
              {Object.entries(PERIOD_LABELS).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
            </select>
          </label>
          {showToken && (
            <label className="ai-chat__token">Access token
              <input ref={tokenRef} type="password" autoComplete="off" value={accessToken} onChange={(event) => setAccessToken(event.target.value)} aria-describedby="ai-chat-token-help" />
            </label>
          )}
          <button type="button" onClick={clear} disabled={messages.length === 0 && !error}>Clear chat</button>
          {showToken && <small id="ai-chat-token-help">ANALYST_ACCESS_TOKEN from the backend, not the AI provider key. Kept in memory only.</small>}
        </div>

        <div className="ai-chat__log" ref={logRef} role="log" aria-live="polite" aria-label="Conversation with the AI energy assistant">
          {messages.length === 0 && !loading && (
            <div className="ai-chat__empty">
              <strong>Ask about this room&apos;s recorded data</strong>
              <p>It reads recorded appliance runtime, assumed-wattage energy estimates, sensor averages and occupancy samples, explains what they mean and suggests better habits. It cannot switch devices.</p>
              <div className="ai-chat__chips">
                {STARTER_QUESTIONS.map((question) => (
                  <button key={question} type="button" disabled={tokenMissing} onClick={() => void ask(question)}>{question}</button>
                ))}
              </div>

            </div>
          )}
          {messages.map((message) => (
            <article key={message.id} data-message-id={message.id} className={`ai-chat__message ai-chat__message--${message.role}`}>
              <span className="ai-chat__author">{message.role === "user" ? "You" : "Assistant"}</span>
              <p>{message.content}</p>
              {message.reply && (
                <span className="ai-chat__meta">
                  {message.reply.period ? `${PERIOD_LABELS[message.reply.period]} data` : "Room data"}
                  {message.reply.metrics_as_of && ` · as of ${new Date(message.reply.metrics_as_of).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}`}
                  {message.reply.data_complete === false && " · period in progress or coverage incomplete"}
                </span>
              )}
            </article>
          ))}
          {loading && (
            <p className="ai-chat__thinking" role="status">
              {waitSeconds !== null
                ? `The AI's free per-minute limit is busy. Answering automatically in ${waitSeconds} s…`
                : "Assistant is reading your room data…"}
            </p>
          )}
          {error && <div className="inline-alert ai-chat__error" role="alert">{error}</div>}
          {lastReply && lastReply.suggested_questions.length > 0 && !loading && (
            <div className="ai-chat__chips ai-chat__chips--follow-up" aria-label="Suggested follow-up questions">
              {lastReply.suggested_questions.map((question) => (
                <button key={question} type="button" disabled={tokenMissing} onClick={() => void ask(question)}>{question}</button>
              ))}
            </div>
          )}
        </div>

        <form className="ai-chat__composer" onSubmit={onSubmit}>
          <label htmlFor="ai-chat-input" className="ai-chat__visually-hidden">Your question</label>
          <textarea
            ref={inputRef}
            id="ai-chat-input"
            rows={2}
            maxLength={MAX_QUESTION_CHARS}
            value={draft}
            placeholder={tokenMissing ? "Enter the access token first" : "Ask about energy, appliances, comfort…"}
            disabled={tokenMissing}
            onChange={(event) => setDraft(event.target.value)}
            onKeyDown={onComposerKeyDown}
            aria-describedby="ai-chat-input-help"
          />
          <div className="ai-chat__composer-foot">
            <small id="ai-chat-input-help">Enter to send · Shift + Enter new line · {draft.length}/{MAX_QUESTION_CHARS}</small>
            <button type="submit" disabled={tokenMissing || loading || !draft.trim()}>{loading ? "Waiting…" : "Send"}</button>
          </div>
        </form>

        <p className="ai-chat__note">Energy figures are estimates (recorded ON time × assumed wattage); no meter is connected. AI answers can be wrong.</p>
      </div>

      <button
        ref={bubbleRef}
        type="button"
        className="ai-chat-bubble"
        aria-expanded={open}
        aria-controls="ai-chat-panel"
        aria-label={open ? "Close AI energy assistant" : unread ? "Open AI energy assistant, new reply" : "Open AI energy assistant"}
        onClick={() => setPanel(!open)}
      >
        {open ? (
          <svg viewBox="0 0 24 24" width="24" height="24" aria-hidden="true"><path d="M6 6l12 12M18 6L6 18" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" /></svg>
        ) : (
          <svg viewBox="0 0 24 24" width="26" height="26" aria-hidden="true"><path d="M4 5.5A2.5 2.5 0 0 1 6.5 3h11A2.5 2.5 0 0 1 20 5.5v8a2.5 2.5 0 0 1-2.5 2.5H10l-4.2 3.6c-.5.4-1.3.1-1.3-.6V16A2.5 2.5 0 0 1 4 13.5z" fill="currentColor" /><circle cx="8.5" cy="9.5" r="1.2" fill="var(--green-deep)" /><circle cx="12" cy="9.5" r="1.2" fill="var(--green-deep)" /><circle cx="15.5" cy="9.5" r="1.2" fill="var(--green-deep)" /></svg>
        )}
        {(unread || (loading && !open)) && <span className="ai-chat-bubble__dot" aria-hidden="true" />}
      </button>
    </>
  );
}
