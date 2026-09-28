"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { getDashboardPollIntervalMs, getDashboardService } from "@/src/api/dashboard-service";
import type {
  DashboardData,
  DeviceControlRequest,
  MockScenario,
} from "@/src/types/dashboard";

export function useDashboard() {
  const service = useMemo(() => getDashboardService(), []);
  const pollIntervalMs = useMemo(() => getDashboardPollIntervalMs(), []);
  const [data, setData] = useState<DashboardData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    let timer: number | undefined;

    const poll = async () => {
      try {
        const snapshot = await service.getInitialState();
        if (active) {
          setData(snapshot);
          setError(null);
        }
      } catch {
        if (active) {
          setError("The classroom feed could not be loaded. Showing the last available readings.");
          setData((current) =>
            current ? { ...current, connection: "disconnected" } : current,
          );
        }
      } finally {
        if (active) {
          setLoading(false);
          timer = window.setTimeout(poll, pollIntervalMs);
        }
      }
    };

    void poll();
    return () => {
      active = false;
      if (timer !== undefined) window.clearTimeout(timer);
    };
  }, [pollIntervalMs, service]);

  const requestControl = useCallback(
    async (request: DeviceControlRequest) => {
      setError(null);
      try {
        await service.requestDeviceControl(request);
      } catch (cause) {
        setError(cause instanceof Error ? cause.message : "Control request failed");
      }
    },
    [service],
  );

  const setScenario = useCallback(
    (scenario: MockScenario) => service.setMockScenario?.(scenario),
    [service],
  );

  return { data, loading, error, requestControl, setScenario };
}
