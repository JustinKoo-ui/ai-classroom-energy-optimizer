"use client";

import { useEffect, useState } from "react";
import type { CSSProperties } from "react";
import type { useOccupancy } from "@/src/hooks/useOccupancy";
import { occupancyInsight } from "@/src/lib/occupancy-insights";

export function AIInsightPanel({ live }: { live: ReturnType<typeof useOccupancy> }) {
  const [now, setNow] = useState<number | null>(null);
  useEffect(() => {
    const tick = () => setNow(Date.now());
    const initial = window.setTimeout(tick, 0);
    const timer = window.setInterval(tick, 1000);
    return () => { window.clearTimeout(initial); window.clearInterval(timer); };
  }, []);

  const view = occupancyInsight(live.enabled ? live.occupancy : null, live.history, now);
  const state = !live.enabled ? "disabled" : !live.loaded ? "connecting"
    : live.error ? "reconnecting" : view.fresh ? "observing" : "unknown";
  const samples = view.trend.samples.slice(-12);
  const maxCount = Math.max(1, ...samples.map((sample) => sample.count));
  const observedAt = live.occupancy?.observed_at;
  const recommendation = !live.enabled ? "Live occupancy is disabled. Enable the live camera feed to populate this card."
    : !view.fresh ? live.error ?? "Waiting for a fresh camera observation. Missing or stale data does not establish an empty room."
      : view.count === 0 ? "No people detected in the latest observation. Vacancy is not yet confirmed; the existing control policy decides device actions."
        : `${view.count} ${view.count === 1 ? "person is" : "people are"} detected in the latest camera observation. Device actions follow the existing occupancy and comfort policy.`;

  return (
    <article className="ai-panel">
      <header className="ai-panel__header">
        <div><p className="eyebrow">Live camera insights</p><h3>AI room intelligence</h3></div>
        <span className={`ai-state ai-state--${view.fresh ? "observing" : "paused"}`}>{state}</span>
      </header>

      <div className="ai-panel__hero">
        <div className="probability-ring"
          style={{ "--probability": `${view.freshness_pct * 3.6}deg` } as CSSProperties}
          aria-label={view.age_seconds === null ? "No camera observation" : `Last camera observation ${view.age_seconds} seconds ago`}>
          <div><strong>{ageLabel(view.age_seconds)}</strong><span>sample age</span></div>
        </div>
        <div className="ai-panel__occupants">
          <span>Occupants detected</span>
          <strong>{view.count ?? "--"}</strong>
          <small>{view.fresh ? "Live anonymous camera count" : "Occupancy unknown"}</small>
          {observedAt && <small>Observed {new Date(observedAt).toLocaleTimeString()}</small>}
        </div>
      </div>

      <div className="trend-strip">
        <div><span>Observed trend · last 60s in this session</span><strong>{view.trend.label}</strong></div>
        <div className="trend-bars ai-live-trend" role="img" aria-label={samples.length ? `Observed counts: ${samples.map((sample) => sample.count).join(", ")}` : "No occupancy trend yet"}>
          {samples.map((sample) => <i key={sample.observed_at} style={{ height: `${sample.count / maxCount * 100}%` }} />)}
        </div>
      </div>

      <div className="ai-confidence">
        <div><span>60-second departure forecast</span><strong>Unavailable</strong></div>
        <p className="ai-panel__note">No trained departure model is connected, so departure probability and prediction confidence are unavailable.</p>
      </div>

      <blockquote>{recommendation}</blockquote>
      <footer><span>Camera polling · every {live.pollIntervalMs / 1000}s</span><strong>Advisory only · policy decides</strong></footer>
    </article>
  );
}

function ageLabel(seconds: number | null): string {
  if (seconds === null) return "--";
  if (seconds >= 3600) return `${Math.floor(seconds / 3600)}h`;
  if (seconds >= 60) return `${Math.floor(seconds / 60)}m`;
  return `${seconds}s`;
}
