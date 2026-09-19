"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { getDashboardService } from "@/src/api/dashboard-service";
import type {
  DashboardData,
  DeviceControlRequest,
  MockScenario,
} from "@/src/types/dashboard";

export function useDashboard() {
  const service = useMemo(() => getDashboardService(), []);
  const [data, setData] = useState<DashboardData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    service
      .getInitialState()
      .then((snapshot) => active && setData(snapshot))
      .catch(() => active && setError("The classroom feed could not be loaded."))
      .finally(() => active && setLoading(false));

    const unsubscribe = service.subscribe((event) => {
      if (!active) return;
      if (event.type === "snapshot.updated") {
        setData(event.data as DashboardData);
      } else if (event.type === "device.state_updated") {
        const updatedDevice = event.data as DashboardData["devices"][number];
        setData((current) =>
          current
            ? {
                ...current,
                devices: current.devices.map((device) =>
                  device.id === updatedDevice.id ? updatedDevice : device,
                ),
              }
            : current,
        );
      }
    });
    return () => {
      active = false;
      unsubscribe();
    };
  }, [service]);

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

