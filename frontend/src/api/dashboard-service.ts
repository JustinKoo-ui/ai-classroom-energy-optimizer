import { FastApiDashboardService } from "./fastapi-dashboard-service";
import { MockDashboardService } from "./mock-dashboard-service";
import type { DashboardService } from "@/src/types/dashboard";

let singleton: DashboardService | null = null;

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

