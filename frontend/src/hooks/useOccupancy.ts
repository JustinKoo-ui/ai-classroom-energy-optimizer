"use client";

import { useEffect, useState } from "react";
import { getCurrentOccupancy } from "@/src/api/occupancy-service";
import { getDashboardPollIntervalMs } from "@/src/api/dashboard-service";
import type { OccupancyState, UUID } from "@/src/types/contracts";
import { appendOccupancySample } from "@/src/lib/occupancy-insights";
import type { OccupancySample } from "@/src/lib/occupancy-insights";

interface OccupancyResult {
  key: string;
  occupancy: OccupancyState | null;
  history: OccupancySample[];
  error: string | null;
}

export function useOccupancy(classroomId: UUID | undefined) {
  const [result, setResult] = useState<OccupancyResult | null>(null);
  const enabled = process.env.NEXT_PUBLIC_USE_LIVE_OCCUPANCY === "true";
  const pollIntervalMs = getDashboardPollIntervalMs();
  const apiBaseUrl = process.env.NEXT_PUBLIC_API_BASE_URL ?? "";
  const key = `${apiBaseUrl}:${classroomId}`;

  useEffect(() => {
    if (!enabled || !classroomId) return;

    let active = true;
    let timer: number | undefined;
    const poll = async () => {
      try {
        const reading = await getCurrentOccupancy(apiBaseUrl, classroomId);
        if (active) {
          const receivedAt = Date.now();
          setResult((previous) => {
            const current = previous?.key === key ? previous : null;
            // Ignore replayed older observations; a stale response for the same
            // timestamp must still be allowed to invalidate a cached count.
            if (current?.occupancy?.observed_at && reading.observed_at
              && Date.parse(reading.observed_at) < Date.parse(current.occupancy.observed_at)) {
              return { ...current, error: null };
            }
            return { key, occupancy: reading, error: null,
              history: appendOccupancySample(current?.history ?? [], reading, receivedAt) };
          });
        }
      } catch (cause) {
        if (active) {
          setResult((previous) => ({ key,
            occupancy: previous?.key === key ? previous.occupancy : null,
            history: previous?.key === key ? previous.history : [],
            error: cause instanceof Error
              ? cause.message
              : "Live occupancy data is unavailable.",
          }));
        }
      } finally {
        if (active) {
          timer = window.setTimeout(poll, pollIntervalMs);
        }
      }
    };

    void poll();
    return () => {
      active = false;
      if (timer !== undefined) window.clearTimeout(timer);
    };
  }, [apiBaseUrl, classroomId, enabled, pollIntervalMs, key]);

  const current = enabled && result?.key === key ? result : null;
  return { occupancy: current?.occupancy ?? null, error: current?.error ?? null,
    loaded: current !== null, enabled, history: current?.history ?? [], pollIntervalMs };
}
