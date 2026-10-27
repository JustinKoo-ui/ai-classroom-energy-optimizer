"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  HardwareControlService,
  getHardwareControlMode,
} from "@/src/api/hardware-control-service";
import type { DeviceControlRequest } from "@/src/types/dashboard";
import type {
  LocalDeviceControlStateList,
  LocalUrgencyStatus,
} from "@/src/types/device-control";
import type { UUID } from "@/src/types/contracts";

export function useLocalHardwareControls(classroomId: UUID | undefined) {
  const mode = useMemo(() => getHardwareControlMode(), []);
  const enabled = mode !== "demo";
  const service = useMemo(
    () => (enabled && classroomId ? new HardwareControlService(classroomId) : null),
    [classroomId, enabled],
  );
  const [deviceStates, setDeviceStates] = useState<LocalDeviceControlStateList | null>(null);
  const [urgency, setUrgency] = useState<LocalUrgencyStatus | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [pendingDeviceIds, setPendingDeviceIds] = useState<ReadonlySet<UUID>>(new Set());

  const refresh = useCallback(async () => {
    if (!service) return;
    try {
      const [next, urgencyStatus] = await Promise.all([
        service.getDeviceStates(),
        service.getUrgencyStatus(),
      ]);
      setDeviceStates(next);
      setUrgency(urgencyStatus);
      setError(null);
    } catch (cause) {
      setDeviceStates(null);
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
        const [next, urgencyStatus] = await Promise.all([
          service.getDeviceStates(),
          service.getUrgencyStatus(),
        ]);
        if (active) {
          setDeviceStates(next);
          setUrgency(urgencyStatus);
          setError(null);
        }
      } catch (cause) {
        if (active) {
          setDeviceStates(null);
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
      if (!service) throw new Error("Hardware controls are not enabled.");
      setError(null);
      setPendingDeviceIds((previous) => new Set(previous).add(request.device_id));
      try {
        await service.requestControl(request);
        await refresh();
      } catch (cause) {
        const message = cause instanceof Error ? cause.message : "ESP32 command failed.";
        setError(message);
      } finally {
        setPendingDeviceIds((previous) => {
          const next = new Set(previous);
          next.delete(request.device_id);
          return next;
        });
      }
    },
    [refresh, service],
  );

  const activateUrgency = useCallback(async () => {
    if (!service || mode !== "local") throw new Error("Urgency activation is available from localhost only.");
    setError(null);
    try {
      const status = await service.activateUrgency();
      setUrgency(status);
      await refresh();
    } catch (cause) {
      const message = cause instanceof Error ? cause.message : "Urgency request failed.";
      setError(message);
    }
  }, [mode, refresh, service]);

  return {
    enabled,
    mode,
    canActivateUrgency: mode === "local",
    pendingDeviceIds,
    deviceStates,
    loaded,
    error,
    urgency,
    requestControl,
    activateUrgency,
    refresh,
  };
}
