"use client";

import { useCallback, useEffect, useState } from "react";
import { getEnergyAnalytics } from "@/src/api/energy-analytics-service";
import type { UUID } from "@/src/types/contracts";
import type { AnalyticsPeriod, AnalyticsReport } from "@/src/types/energy-analytics";

interface AnalyticsResult {
  key: string;
  report: AnalyticsReport | null;
  error: string | null;
}

export function useEnergyAnalytics(classroomId: UUID, period: AnalyticsPeriod, date: string) {
  const [result, setResult] = useState<AnalyticsResult | null>(null);
  const [revision, setRevision] = useState(0);
  const key = `${classroomId}:${period}:${date}`;

  useEffect(() => {
    let active = true;
    let timer: number | undefined;
    let controller: AbortController | undefined;
    const poll = async () => {
      controller = new AbortController();
      try {
        const report = await getEnergyAnalytics(classroomId, period, date, controller.signal);
        if (active) setResult({ key, report, error: null });
      } catch (cause) {
        if (active && !controller.signal.aborted) {
          setResult((previous) => ({
            key,
            report: previous?.key === key ? previous.report : null,
            error: cause instanceof Error ? cause.message : "Runtime analytics are unavailable.",
          }));
        }
      } finally {
        if (active) timer = window.setTimeout(poll, 30_000);
      }
    };
    void poll();
    return () => {
      active = false;
      controller?.abort();
      if (timer !== undefined) window.clearTimeout(timer);
    };
  }, [classroomId, period, date, key, revision]);

  const refresh = useCallback(() => setRevision((current) => current + 1), []);
  return {
    report: result?.key === key ? result.report : null,
    error: result?.key === key ? result.error : null,
    loading: result?.key !== key,
    refresh,
  };
}
