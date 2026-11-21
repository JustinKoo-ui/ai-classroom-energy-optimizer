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

export function AIChat({ classroomId }: { classroomId: UUID }) {
  // The access token stays in React memory only, like the analyst report token.
  // It is not the provider API key and is never written to browser storage.
  const [accessToken, setAccessToken] = useState("");
  const [period, setPeriod] = useState<AnalyticsPeriod>("day");
  const [draft, setDraft] = useState("");
  const { messages, error, loading, send, clear } = useAIChat(classroomId);
  const logRef = useRef<HTMLDivElement>(null);
  const tokenMissing = !accessToken.trim();
  const lastReply = messages.at(-1)?.reply;

  useEffect(() => {
    const log = logRef.current;
    if (log) log.scrollTop = log.scrollHeight;
  }, [messages, loading]);

  async function ask(question: string) {
    if (tokenMissing || loading || !question.trim()) return;
    setDraft("");
    if (!(await send(question, accessToken, period))) setDraft(question);
  }

  function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    void ask(draft);
  }

  function onKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
      event.preventDefault();
      void ask(draft);
    }
  }

  return (
    <section id="ai-chat" className="ai-chat" aria-labelledby="ai-chat-title">
      <div className="section-heading">
        <div><p className="eyebrow">Ask about your room</p><h2 id="ai-chat-title">AI energy assistant</h2></div>
        <p>Read-only · it cannot switch devices</p>
      </div>

      <div className="analyst-toolbar ai-chat__setup">
        <label>Data period
          <select value={period} onChange={(event) => setPeriod(event.target.value as AnalyticsPeriod)}>
            {Object.entries(PERIOD_LABELS).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
          </select>
        </label>
        <label className="ai-chat__token">Report access token
          <input type="password" autoComplete="off" value={accessToken} onChange={(event) => setAccessToken(event.target.value)} aria-describedby="ai-chat-token-help" />
        </label>
        <button type="button" onClick={clear} disabled={messages.length === 0 && !error}>Clear chat</button>
        <small id="ai-chat-token-help">The same separate token used by the Environmental analyst. Held in memory for this page session only.</small>
      </div>

      <div className="ai-chat__log" ref={logRef} role="log" aria-live="polite" aria-label="Conversation with the AI energy assistant">
        {messages.length === 0 && !loading && (
          <div className="ai-chat__empty">
            <strong>Ask anything about this room&apos;s recorded data</strong>
            <p>The assistant reads the same recorded runtime, assumed-wattage energy estimates, sensor averages and occupancy samples as the Environmental analyst, explains what they mean, and suggests better habits.</p>
            <div className="ai-chat__chips">
              {STARTER_QUESTIONS.map((question) => (
                <button key={question} type="button" disabled={tokenMissing} onClick={() => void ask(question)}>{question}</button>
              ))}
            </div>
            {tokenMissing && <small>Enter the report access token above to start.</small>}
          </div>
        )}
        {messages.map((message) => (
          <article key={message.id} className={`ai-chat__message ai-chat__message--${message.role}`}>
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
        {loading && <p className="ai-chat__thinking" role="status">Assistant is reading your room data…</p>}
      </div>

      {error && <div className="inline-alert" role="alert">{error}</div>}

      {lastReply && lastReply.suggested_questions.length > 0 && !loading && (
        <div className="ai-chat__chips ai-chat__chips--follow-up" aria-label="Suggested follow-up questions">
          {lastReply.suggested_questions.map((question) => (
            <button key={question} type="button" disabled={tokenMissing} onClick={() => void ask(question)}>{question}</button>
          ))}
        </div>
      )}

      <form className="ai-chat__composer" onSubmit={onSubmit}>
        <label htmlFor="ai-chat-input" className="ai-chat__visually-hidden">Your question</label>
        <textarea
          id="ai-chat-input"
          rows={2}
          maxLength={MAX_QUESTION_CHARS}
          value={draft}
          placeholder={tokenMissing ? "Enter the access token above first" : "Ask about energy, appliances, comfort or occupancy…"}
          disabled={tokenMissing}
          onChange={(event) => setDraft(event.target.value)}
          onKeyDown={onKeyDown}
          aria-describedby="ai-chat-input-help"
        />
        <div className="ai-chat__composer-foot">
          <small id="ai-chat-input-help">Enter to send · Shift + Enter for a new line · {draft.length}/{MAX_QUESTION_CHARS}</small>
          <button type="submit" disabled={tokenMissing || loading || !draft.trim()}>{loading ? "Waiting…" : "Send"}</button>
        </div>
      </form>

      <p className="ai-chat__note">
        Energy figures are estimates from recorded ON time × assumed wattage; no electricity meter is connected.
        AI answers can be wrong, so check important figures on the Environmental analyst page.
      </p>
    </section>
  );
}
