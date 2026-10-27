import type { UUID } from "@/src/types/contracts";
import type { AnalyticsPeriod, AnalyticsReport } from "@/src/types/energy-analytics";

export async function getEnergyAnalytics(
  classroomId: UUID,
  period: AnalyticsPeriod,
  date: string,
  signal: AbortSignal,
): Promise<AnalyticsReport> {
  const query = new URLSearchParams({ period });
  if (date) query.set("date", date);
  const baseUrl = process.env.NEXT_PUBLIC_API_BASE_URL ?? "";
  const response = await fetch(
    `${baseUrl}/api/v1/classrooms/${classroomId}/analytics?${query}`,
    { credentials: "include", cache: "no-store", signal: AbortSignal.any([signal, AbortSignal.timeout(15_000)]) },
  );
  if (!response.ok) {
    const body = (await response.json().catch(() => null)) as
      | { detail?: unknown }
      | null;
    throw new Error(
      typeof body?.detail === "string"
        ? body.detail
        : `Runtime analytics are unavailable (${response.status}).`,
    );
  }
  return (await response.json()) as AnalyticsReport;
}
