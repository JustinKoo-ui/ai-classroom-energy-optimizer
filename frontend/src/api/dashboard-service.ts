import { FastApiDashboardService } from "./fastapi-dashboard-service";
import { MockDashboardService } from "./mock-dashboard-service";
import type { DashboardService } from "@/src/types/dashboard";

const DEFAULT_DASHBOARD_POLL_INTERVAL_MS = 5_000;

let singleton: DashboardService | null = null;

export function getDashboardPollIntervalMs(): number {
  const configuredInterval = process.env.NEXT_PUBLIC_DASHBOARD_POLL_INTERVAL_MS;
  if (configuredInterval === undefined) return DEFAULT_DASHBOARD_POLL_INTERVAL_MS;

  const interval = Number(configuredInterval);
  if (!Number.isSafeInteger(interval) || interval < 1_000) {
    throw new Error("NEXT_PUBLIC_DASHBOARD_POLL_INTERVAL_MS must be an integer of at least 1000");
  }
  return interval;
}

export function getDashboardService(): DashboardService {
  if (singleton) return singleton;
  const useMock = process.env.NEXT_PUBLIC_USE_MOCK_DATA !== "false";
  singleton = useMock
    ? new MockDashboardService()
    : new FastApiDashboardService(
        process.env.NEXT_PUBLIC_API_BASE_URL ?? window.location.origin,
        process.env.NEXT_PUBLIC_CLASSROOM_ID ?? "26e97d46-7a34-4439-922a-83684bda557d",
      );
  return singleton;
}
