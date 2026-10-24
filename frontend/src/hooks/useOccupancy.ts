"use client";

import { useEffect, useState } from "react";
import { getCurrentOccupancy } from "@/src/api/occupancy-service";
import { getDashboardPollIntervalMs } from "@/src/api/dashboard-service";
import type { OccupancyState, UUID } from "@/src/types/contracts";

export function useOccupancy(classroomId: UUID | undefined) {
  const [occupancy, setOccupancy] = useState<OccupancyState | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loaded, setLoaded] = useState(false);
  const enabled = process.env.NEXT_PUBLIC_USE_LIVE_OCCUPANCY === "true";
  const pollIntervalMs = getDashboardPollIntervalMs();
  const apiBaseUrl = process.env.NEXT_PUBLIC_API_BASE_URL ?? "";

  useEffect(() => {
    if (!enabled || !classroomId) return;

    let active = true;
    let timer: number | undefined;
    const poll = async () => {
      try {
        const result = await getCurrentOccupancy(apiBaseUrl, classroomId);
        if (active) {
          setOccupancy(result);
          setError(null);
        }
      } catch (cause) {
        if (active) {
          setError(
            cause instanceof Error
              ? cause.message
              : "Live occupancy data is unavailable.",
          );
        }
      } finally {
        if (active) {
          setLoaded(true);
          timer = window.setTimeout(poll, pollIntervalMs);
        }
      }
    };

    void poll();
    return () => {
      active = false;
      if (timer !== undefined) window.clearTimeout(timer);
    };
  }, [apiBaseUrl, classroomId, enabled, pollIntervalMs]);

  return { occupancy, error, loaded, enabled };
}
