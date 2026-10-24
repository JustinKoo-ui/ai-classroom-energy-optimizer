"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  HardwareControlService,
  isLocalHardwareControlEnabled,
} from "@/src/api/hardware-control-service";
import type { DeviceControlRequest } from "@/src/types/dashboard";
import type { LocalDeviceControlStateList } from "@/src/types/device-control";
import type { UUID } from "@/src/types/contracts";

export function useLocalHardwareControls(classroomId: UUID | undefined) {
  const enabled = useMemo(() => isLocalHardwareControlEnabled(), []);
  const service = useMemo(
    () => (enabled && classroomId ? new HardwareControlService(classroomId) : null),
    [classroomId, enabled],
  );
  const [deviceStates, setDeviceStates] = useState<LocalDeviceControlStateList | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    if (!service) return;
    try {
      const next = await service.getDeviceStates();
      setDeviceStates(next);
      setError(null);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not load ESP32 state.");
    } finally {
      setLoaded(true);
    }
  }, [service]);

  useEffect(() => {
    if (!service) return;
    let active = true;
    let timer: number | undefined;

    const poll = async () => {
      try {
        const next = await service.getDeviceStates();
        if (active) {
          setDeviceStates(next);
          setError(null);
        }
      } catch (cause) {
        if (active) {
          setError(cause instanceof Error ? cause.message : "Could not load ESP32 state.");
        }
      } finally {
        if (active) {
          setLoaded(true);
          timer = window.setTimeout(poll, 2000);
        }
      }
    };
    void poll();
    return () => {
      active = false;
      if (timer !== undefined) window.clearTimeout(timer);
    };
  }, [service]);

  const requestControl = useCallback(
    async (request: DeviceControlRequest) => {
      if (!service) throw new Error("Local hardware controls are not enabled.");
      setError(null);
      try {
        await service.requestControl(request);
        await refresh();
      } catch (cause) {
        const message = cause instanceof Error ? cause.message : "ESP32 command failed.";
        setError(message);
        throw new Error(message);
      }
    },
    [refresh, service],
  );

  return { enabled, deviceStates, loaded, error, requestControl, refresh };
}
