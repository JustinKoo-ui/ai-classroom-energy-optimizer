import type { AIInsight } from "@/src/types/dashboard";

const trendLabels: Record<AIInsight["predicted_occupancy_trend"], string> = {
  rising: "Occupancy rising",
  stable: "Occupancy stable",
  falling: "Occupancy falling",
};

export function AIInsightPanel({ insight }: { insight: AIInsight }) {
  const departurePct = Math.round(insight.departure_probability * 100);
  const confidencePct = Math.round(insight.prediction_confidence * 100);
  const trendBars =
    insight.predicted_occupancy_trend === "rising"
      ? [28, 38, 48, 62, 76]
      : insight.predicted_occupancy_trend === "falling"
        ? [76, 64, 51, 39, 27]
        : [52, 55, 51, 54, 52];

  return (
    <article className="ai-panel" id="ai-insights">
      <header className="ai-panel__header">
        <div>
          <p className="eyebrow">Prediction engine</p>
          <h3>AI room intelligence</h3>
        </div>
        <span className={`ai-state ai-state--${insight.ai_control_state}`}>
          {insight.ai_control_state}
        </span>
      </header>

      <div className="ai-panel__hero">
        <div
          className="probability-ring"
          style={{ "--probability": `${departurePct * 3.6}deg` } as React.CSSProperties}
          aria-label={`${departurePct}% departure probability`}
        >
          <div>
            <strong>{departurePct}%</strong>
            <span>leave in 60s</span>
          </div>
        </div>
        <div className="ai-panel__occupants">
          <span>Occupants detected</span>
          <strong>{insight.occupants_detected}</strong>
          <small>Anonymous person tracks</small>
        </div>
      </div>

      <div className="trend-strip">
        <div>
          <span>Predicted trend</span>
          <strong>{trendLabels[insight.predicted_occupancy_trend]}</strong>
        </div>
        <div className="trend-bars" aria-hidden="true">
          {trendBars.map((height, index) => <i key={index} style={{ height: `${height}%` }} />)}
        </div>
      </div>

      <div className="ai-confidence">
        <div><span>Prediction confidence</span><strong>{confidencePct}%</strong></div>
        <div className="confidence-track"><i style={{ width: `${confidencePct}%` }} /></div>
      </div>

      <blockquote>{insight.recommendation}</blockquote>
      <footer>
        <span>{insight.model_version}</span>
        <strong>Advisory only · policy decides</strong>
      </footer>
    </article>
  );
}

