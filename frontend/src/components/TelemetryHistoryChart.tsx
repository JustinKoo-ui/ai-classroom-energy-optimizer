"use client";

import { useEffect, useMemo, useRef } from "react";

type HistorySample = {
  received_at: string;
  payload?: { boot_id: string | null };
};

type ChartSeries<Sample> = {
  label: string;
  color: string;
  dashed?: boolean;
  gapMs?: number;
  // Undefined belongs to a different series. Null is a missing reading in this series.
  get: (sample: Sample) => number | null | undefined;
};

export type HistoryChartDefinition<Sample> = {
  title: string;
  description?: string;
  unit: string;
  precision: number;
  series: ChartSeries<Sample>[];
  breakAt?: "energy-reset" | "azimuth-wrap";
};

export function TelemetryHistoryChart<Sample extends HistorySample>({ definition, history, gapMs = 6_000 }: {
  definition: HistoryChartDefinition<Sample>;
  history: Sample[];
  gapMs?: number;
}) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const { title, description, unit, precision, series, breakAt } = definition;
  const points = useMemo(() => history
    .map((sample) => ({ sample, time: Date.parse(sample.received_at) }))
    .filter(({ time }) => Number.isFinite(time)), [history]);
  const measuredSeries = useMemo(() => series.map((item) => ({
    ...item,
    points: points.flatMap(({ sample, time }) => {
      const value = item.get(sample);
      return value === undefined ? [] : [{ time, value, bootId: sample.payload?.boot_id }];
    }),
  })), [points, series]);
  const values = measuredSeries.flatMap((item) => item.points.map(({ value }) => value))
    .filter((value): value is number => value !== null && Number.isFinite(value));
  const hasValues = values.length > 0;
  const min = hasValues ? Math.min(...values) : 0;
  const max = hasValues ? Math.max(...values) : 0;
  const from = points[0]?.time ?? 0;
  const to = points.at(-1)?.time ?? from;

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas || !hasValues) return;
    const context = canvas.getContext("2d");
    if (!context) return;

    function draw() {
      if (!canvas || !context) return;
      const { width, height } = canvas.getBoundingClientRect();
      if (!width || !height) return;
      const ratio = window.devicePixelRatio || 1;
      canvas.width = Math.round(width * ratio);
      canvas.height = Math.round(height * ratio);
      context.setTransform(ratio, 0, 0, ratio, 0, 0);

      const inset = 5;
      const duration = Math.max(1, to - from);
      const range = max - min;
      const plotWidth = width - 2 * inset;
      const plotHeight = height - 2 * inset;
      context.strokeStyle = "#dfe7df";
      context.lineWidth = 1;
      context.setLineDash([3, 4]);
      for (const fraction of [0, 0.5, 1]) {
        const y = inset + fraction * plotHeight;
        context.beginPath();
        context.moveTo(inset, y);
        context.lineTo(width - inset, y);
        context.stroke();
      }

      for (const item of measuredSeries) {
        context.strokeStyle = item.color;
        context.fillStyle = item.color;
        context.lineWidth = 2;
        context.lineJoin = "round";
        context.setLineDash(item.dashed ? [6, 4] : []);
        const markers: { x: number; y: number }[] = [];
        let previous: { value: number; time: number; bootId: string | null | undefined } | null = null;
        context.beginPath();
        for (const { value, time, bootId } of item.points) {
          if (value === null || !Number.isFinite(value)) {
            previous = null;
            continue;
          }

          const elapsed = to === from ? 0.5 : (time - from) / duration;
          const position = range === 0 ? 0.5 : (value - min) / range;
          const x = inset + elapsed * plotWidth;
          const y = height - inset - position * plotHeight;
          const boundary = previous && (
            (previous.bootId != null && bootId != null && previous.bootId !== bootId) ||
            time - previous.time > (item.gapMs ?? gapMs) ||
            (breakAt === "energy-reset" && value < previous.value) ||
            (breakAt === "azimuth-wrap" && Math.abs(value - previous.value) > 180)
          );
          if (previous && !boundary) {
            context.lineTo(x, y);
          } else {
            context.moveTo(x, y);
            markers.push({ x, y });
          }
          previous = { value, time, bootId };
        }
        context.stroke();
        for (const { x, y } of markers) {
          context.beginPath();
          context.arc(x, y, 2.5, 0, 2 * Math.PI);
          context.fill();
        }
      }
    }

    draw();
    const observer = new ResizeObserver(draw);
    observer.observe(canvas);
    return () => observer.disconnect();
  }, [breakAt, from, gapMs, hasValues, max, measuredSeries, min, to]);

  const timeTicks = to === from ? [from] : to - from < 10_000 ? [from, to] : [from, (from + to) / 2, to];
  const showDate = new Date(from).toDateString() !== new Date(to).toDateString();
  const formatValue = (value: number) => Number(value.toFixed(precision)).toString();
  return (
    <article className="chart-card solar-chart">
      <header className="chart-card__header">
        <h3>{title}</h3>
        <span>{unit}</span>
      </header>
      {description && <p className="solar-chart__description">{description}</p>}
      {hasValues && (
        <div className="solar-chart__plot">
          <div className="solar-chart__axis" aria-hidden="true">
            <span>{formatValue(max)}</span>
            {min !== max && <span>{formatValue(min)}</span>}
          </div>
          <canvas
            ref={canvasRef}
            role="img"
            aria-label={`${title}, observed range ${formatValue(min)} to ${formatValue(max)} ${unit}`}
          />
          <div className="solar-chart__timestamps">
            {timeTicks.map((time) => (
              <time key={time} dateTime={new Date(time).toISOString()}>
                {showDate && <span>{new Date(time).toLocaleDateString([], { month: "short", day: "numeric" })}</span>}
                {new Date(time).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false })}
              </time>
            ))}
          </div>
        </div>
      )}
      {!hasValues && <p className="solar-chart__empty">Waiting for recorded samples.</p>}
      {series.length > 1 && (
        <div className="solar-chart__legend">
          {measuredSeries.map(({ label, color, dashed, points: samples }) => (
            <span key={label}>
              <i style={{ borderColor: color, borderTopStyle: dashed ? "dashed" : "solid" }} />
              {label}{hasValues && !samples.some(({ value }) => value !== null && Number.isFinite(value)) && " · no readings"}
            </span>
          ))}
        </div>
      )}
    </article>
  );
}
