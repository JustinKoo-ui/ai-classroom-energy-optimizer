"use client";

import { useEffect, useState } from "react";
import { classroomTelemetryView, mergeClassroomMeterHistory } from "@/src/lib/classroom-telemetry";
import type { ClassroomMeterPoint, PrototypeMonitorSnapshot, SolarTelemetry } from "@/src/types/prototype-monitor";

const POLL_INTERVAL_MS = 5_000;
const STREAM_TIMEOUT_MS = 12_000;
const HISTORY_LIMIT = 120;

const emptyFeed = {
  endpoint: "",
  snapshot: null as PrototypeMonitorSnapshot | null,
  history: [] as SolarTelemetry[],
  classroomMeterHistory: [] as ClassroomMeterPoint[],
  error: null as string | null,
};

function mergeHistory(
  current: SolarTelemetry[],
  snapshot: PrototypeMonitorSnapshot,
): SolarTelemetry[] {
  const latest = snapshot.solar.freshness === "fresh" ? snapshot.solar.telemetry : null;
  if (!snapshot.history && (!latest || latest.message_id === current.at(-1)?.message_id)) return current;
  const samples = new Map(current.map((sample) => [sample.message_id, sample]));
  for (const sample of snapshot.history ?? []) samples.set(sample.message_id, sample);
  if (latest) samples.set(latest.message_id, latest);
  return [...samples.values()]
    .sort((a, b) => Date.parse(a.received_at) - Date.parse(b.received_at))
    .slice(-HISTORY_LIMIT);
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function parseSnapshot(value: unknown): PrototypeMonitorSnapshot {
  if (!isRecord(value) || typeof value.broker_connected !== "boolean" ||
    !isRecord(value.solar) || !isRecord(value.relay) ||
    (value.history != null && !Array.isArray(value.history)) ||
    (value.energy_history != null && !Array.isArray(value.energy_history)) ||
    (value.classroom_meter_history != null && !Array.isArray(value.classroom_meter_history))
  ) {
    throw new Error("The monitoring API did not return a prototype snapshot.");
  }

  const classroom = value.classroom_telemetry;
  if (classroom != null && (!isRecord(classroom) || !isRecord(classroom.payload) ||
    !isRecord(classroom.payload.sensors) || !isRecord(classroom.payload.relays)
  )) {
    throw new Error("The monitoring API did not return valid classroom telemetry.");
  }
  return value as unknown as PrototypeMonitorSnapshot;
}

export function usePrototypeMonitor(classroomId: string | undefined) {
  const [state, setFeed] = useState(emptyFeed);
  const [now, setNow] = useState<number | null>(null);
  const apiBaseUrl = process.env.NEXT_PUBLIC_API_BASE_URL ?? "";
  const endpoint = classroomId
    ? `${apiBaseUrl.replace(/\/$/, "")}/api/v1/classrooms/${encodeURIComponent(classroomId)}/prototype-monitor`
    : "";
  const feed = state.endpoint === endpoint ? state : emptyFeed;
  const classroom = classroomTelemetryView(feed.snapshot, feed.error, now);

  useEffect(() => {
    if (!endpoint) return;
    const timer = window.setInterval(() => setNow(Date.now()), 1_000);
    return () => window.clearInterval(timer);
  }, [endpoint]);

  useEffect(() => {
    if (!endpoint) return;
    const lifecycle = new AbortController();
    let source: EventSource | undefined;
    let timer: number | undefined;
    let lastStreamSnapshot = -Infinity;
    let streamOpenedAt = 0;

    function accept(snapshot: PrototypeMonitorSnapshot) {
      setFeed((previous) => ({
        endpoint,
        snapshot,
        history: mergeHistory(previous.endpoint === endpoint ? previous.history : [], snapshot),
        classroomMeterHistory: mergeClassroomMeterHistory(
          previous.endpoint === endpoint ? previous.classroomMeterHistory : [],
          snapshot,
        ),
        error: null,
      }));
    }

    function connectStream() {
      if (typeof EventSource === "undefined") return;
      source?.close();
      const stream = new EventSource(`${endpoint}/stream`, { withCredentials: true });
      source = stream;
      streamOpenedAt = performance.now();
      stream.addEventListener("snapshot", (event: MessageEvent<string>) => {
        if (lifecycle.signal.aborted || source !== stream) return;
        try {
          const snapshot = parseSnapshot(JSON.parse(event.data));
          lastStreamSnapshot = performance.now();
          accept(snapshot);
        } catch {
          // The next poll can recover if an intermediary interrupted the stream.
          stream.close();
        }
      });
      stream.onerror = () => {
        if (!lifecycle.signal.aborted && source === stream) lastStreamSnapshot = -Infinity;
      };
    }

    async function poll() {
      if (performance.now() - lastStreamSnapshot > STREAM_TIMEOUT_MS) {
        const latestStreamAtStart = lastStreamSnapshot;
        try {
          const response = await fetch(endpoint, {
            credentials: "include",
            cache: "no-store",
            signal: AbortSignal.any([lifecycle.signal, AbortSignal.timeout(4_000)]),
          });
          if (!response.ok) throw new Error(`Monitoring request failed (${response.status}).`);
          const snapshot = parseSnapshot(await response.json());
          if (!lifecycle.signal.aborted && latestStreamAtStart === lastStreamSnapshot) accept(snapshot);
        } catch (cause) {
          if (!lifecycle.signal.aborted && latestStreamAtStart === lastStreamSnapshot) {
            setFeed((previous) => ({
              ...(previous.endpoint === endpoint ? previous : emptyFeed),
              endpoint,
              error: cause instanceof Error && cause.message.startsWith("Monitoring request failed")
                ? cause.message
                : "Could not reach the monitoring API.",
            }));
          }
        }
        if (!lifecycle.signal.aborted && (
          !source || source.readyState === EventSource.CLOSED ||
          performance.now() - Math.max(streamOpenedAt, lastStreamSnapshot) > STREAM_TIMEOUT_MS
        )) connectStream();
      }
      if (!lifecycle.signal.aborted) timer = window.setTimeout(poll, POLL_INTERVAL_MS);
    }

    void poll();
    return () => {
      lifecycle.abort();
      source?.close();
      if (timer !== undefined) window.clearTimeout(timer);
    };
  }, [endpoint]);

  return { ...feed, classroom };
}
