import type { UUID } from "@/src/types/contracts";
import type { AnalyticsPeriod } from "@/src/types/energy-analytics";
import type { AnalystResponse } from "@/src/types/environmental-analyst";

export async function generateEnvironmentalReport(
  classroomId: UUID,
  period: AnalyticsPeriod,
  date: string | null,
  refresh: boolean,
  accessToken: string,
  signal: AbortSignal,
): Promise<AnalystResponse> {
  const baseUrl = process.env.NEXT_PUBLIC_API_BASE_URL ?? "";
  const response = await fetch(
    `${baseUrl}/api/v1/classrooms/${classroomId}/analytics/report`,
    {
      method: "POST",
      credentials: "include",
      cache: "no-store",
      signal: AbortSignal.any([signal, AbortSignal.timeout(90_000)]),
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${accessToken.trim()}`,
      },
      body: JSON.stringify({ date, period, refresh }),
    },
  );
  if (!response.ok) {
    const body = (await response.json().catch(() => null)) as
      | { detail?: unknown }
      | null;
    if (response.status === 401) {
      throw new Error("Report access was not authorized. Check the separate local report access token.");
    }
    throw new Error(
      typeof body?.detail === "string"
        ? body.detail
        : `AI commentary is temporarily unavailable (${response.status}). Runtime and energy calculations remain available.`,
    );
  }
  return (await response.json()) as AnalystResponse;
}
