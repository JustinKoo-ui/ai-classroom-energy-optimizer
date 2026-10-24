"use client";

import { useEffect, useState } from "react";
import { getLatestEnvironmentTelemetry } from "@/src/api/environment-telemetry-service";
import { getDashboardPollIntervalMs } from "@/src/api/dashboard-service";
import type { LatestEnvironmentTelemetry } from "@/src/types/environment-telemetry";

const DEFAULT_CLASSROOM_ID = "26e97d46-7a34-4439-922a-83684bda557d";

export function useEnvironmentTelemetry() {
  const [telemetry, setTelemetry] = useState<LatestEnvironmentTelemetry | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loaded, setLoaded] = useState(false);
  const pollIntervalMs = getDashboardPollIntervalMs();
  const apiBaseUrl = process.env.NEXT_PUBLIC_API_BASE_URL ?? "";
  const classroomId = process.env.NEXT_PUBLIC_CLASSROOM_ID ?? DEFAULT_CLASSROOM_ID;

  useEffect(() => {
    let active = true;
    let timer: number | undefined;

    const poll = async () => {
      try {
        const result = await getLatestEnvironmentTelemetry(apiBaseUrl, classroomId);
        if (active) {
          setTelemetry(result);
          setError(null);
        }
      } catch (cause) {
        if (active) {
          setError(
            cause instanceof Error ? cause.message : "Live sensor data is unavailable.",
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
  }, [apiBaseUrl, classroomId, pollIntervalMs]);

  return { telemetry, error, loaded };
}
