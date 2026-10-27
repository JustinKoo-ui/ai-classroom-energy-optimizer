"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { generateEnvironmentalReport } from "@/src/api/environmental-analyst-service";
import type { UUID } from "@/src/types/contracts";
import type { AnalyticsPeriod } from "@/src/types/energy-analytics";
import type { AnalystResponse } from "@/src/types/environmental-analyst";

// The caller keys its request component by reporting context so changing the
// period, date, or classroom unmounts it and aborts the previous request.
export function useAnalystReport(classroomId: UUID, period: AnalyticsPeriod, date: string | null) {
  const [report, setReport] = useState<AnalystResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const controllerRef = useRef<AbortController | null>(null);
  const requestRef = useRef(0);

  useEffect(() => () => controllerRef.current?.abort(), []);

  const generate = useCallback(async (accessToken: string, refresh: boolean) => {
    controllerRef.current?.abort();
    const controller = new AbortController();
    controllerRef.current = controller;
    const requestId = ++requestRef.current;
    setLoading(true);
    setError(null);
    try {
      const next = await generateEnvironmentalReport(
        classroomId, period, date, refresh, accessToken, controller.signal,
      );
      if (!controller.signal.aborted && requestId === requestRef.current) setReport(next);
    } catch (cause) {
      if (!controller.signal.aborted && requestId === requestRef.current) {
        setError(cause instanceof Error ? cause.message : "AI commentary is temporarily unavailable.");
      }
    } finally {
      if (!controller.signal.aborted && requestId === requestRef.current) setLoading(false);
    }
  }, [classroomId, period, date]);

  return { report, error, loading, generate };
}
