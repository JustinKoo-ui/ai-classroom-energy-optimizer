"use client";

import { useState } from "react";
import { useAnalystReport } from "@/src/hooks/useAnalystReport";
import type { UUID } from "@/src/types/contracts";
import type { AnalyticsPeriod } from "@/src/types/energy-analytics";
import type { AnalystSections } from "@/src/types/environmental-analyst";

interface CommentaryProps {
  classroomId: UUID;
  period: AnalyticsPeriod;
  date: string | null;
  currentReportingDate: string;
  timezone: string | null;
  metricsAvailable: boolean;
}

export function AnalystCommentary(props: CommentaryProps) {
  // This separate local access token stays in React memory only. It is not the
  // provider API key and is never read from or written to browser storage.
  const [accessToken, setAccessToken] = useState("");
  return (
    <article className="chart-card analyst-commentary" aria-labelledby="analyst-commentary-title">
      <p className="eyebrow">Layer 2 · AI-generated commentary</p>
      <h3 id="analyst-commentary-title">Environmental interpretation</h3>
      <p>Generate commentary from the calculated analytics and aggregate environmental observations. Numerical results above remain available when AI commentary is unavailable.</p>
      <div className="analyst-token-field">
        <label htmlFor="analyst-access-token">Report access token</label>
        <input id="analyst-access-token" type="password" autoComplete="off" value={accessToken} onChange={(event) => setAccessToken(event.target.value)} aria-describedby="analyst-token-help" />
        <small id="analyst-token-help">Use the separate local report access token configured on your backend. Held in memory for this page session. Your AI provider API key stays on the server.</small>
      </div>
      <CommentaryRequest key={`${props.classroomId}:${props.period}:${props.date ?? props.currentReportingDate}`} {...props} accessToken={accessToken} />
    </article>
  );
}

function CommentaryRequest({ classroomId, period, date, timezone, metricsAvailable, accessToken }: CommentaryProps & { accessToken: string }) {
  const { report, error, loading, generate } = useAnalystReport(classroomId, period, date);
  const disabled = loading || !accessToken.trim() || !metricsAvailable;
  return <>
    <div className="analyst-toolbar analyst-report-actions">
      <button type="button" disabled={disabled} onClick={() => void generate(accessToken, false)}>{loading ? "Generating commentary…" : "Generate AI report"}</button>
      <button type="button" disabled={disabled} onClick={() => void generate(accessToken, true)}>Refresh AI report</button>
      <span>No automatic AI requests · generation may use the configured provider</span>
    </div>
    {!metricsAvailable && <p role="status">Load the recorded analytics before generating commentary.</p>}
    {loading && <p role="status">AI commentary is being generated. Dashboard monitoring and calculated analytics continue independently.</p>}
    {error && <div className="inline-alert" role="alert">{error} You can retry report generation.</div>}
    {report && <>
      <p className={`analyst-report-message analyst-report-message--${report.status}`} role="status">{report.message}{report.retry_after_seconds !== null && ` Retry after ${report.retry_after_seconds} seconds.`}</p>
      <dl className="analyst-report-metadata">
        <div><dt>Commentary status</dt><dd>{report.cached ? "Cached report" : "Fresh response"} · {report.status}</dd></div>
        <div><dt>Generated</dt><dd>{reportTimestamp(report.generated_at, timezone)}</dd></div>
        <div><dt>Numerical snapshot as of</dt><dd>{reportTimestamp(report.metrics_as_of, timezone)}</dd></div>
        <div><dt>Provider / model</dt><dd>{report.provider ?? "Unavailable"} / {report.model ?? "Unavailable"}</dd></div>
      </dl>
      {report.status === "ready" && <p className="analyst-method-note">This commentary describes the numerical snapshot timestamp shown here. The independently refreshed metrics above may contain newer observations.</p>}
      {report.status === "ready" && report.sections && <ReportSections sections={report.sections} />}
      {(report.mandatory_disclosures.length > 0 || report.sections) && <details className="analyst-details analyst-methodology">
        <summary>Methodology and limitations</summary>
        {report.status === "ready" && report.sections && <p>{report.sections.data_quality_and_limitations}</p>}
        <ul>{report.mandatory_disclosures.map((disclosure, index) => <li key={index}>{disclosure}</li>)}</ul>
      </details>}
    </>}
  </>;
}

function ReportSections({ sections }: { sections: AnalystSections }) {
  const paragraphs = [
    ["Executive summary", sections.executive_summary],
    ["Environmental analysis", sections.environmental_analysis],
    ["Device runtime analysis", sections.device_runtime_analysis],
    ["Energy analysis", sections.energy_analysis],
    ["Automation observations", sections.automation_observations],
  ];
  return <div className="analyst-prose">
    {paragraphs.map(([heading, body]) => <section key={heading}><h4>{heading}</h4><p>{body}</p></section>)}
    <section><h4>Recommendations</h4><ul>{sections.recommendations.map((recommendation, index) => <li key={index}>{recommendation}</li>)}</ul></section>
  </div>;
}

function reportTimestamp(value: string | null, timezone: string | null): string {
  if (!value) return "Unavailable";
  const display = new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "medium", ...(timezone ? { timeZone: timezone } : {}) }).format(new Date(value));
  return `${display}${timezone ? ` · ${timezone}` : ""} (${value})`;
}
