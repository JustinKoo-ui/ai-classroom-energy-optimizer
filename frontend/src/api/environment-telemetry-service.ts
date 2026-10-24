import type { UUID } from "@/src/types/contracts";
import type { LatestEnvironmentTelemetry } from "@/src/types/environment-telemetry";

export async function getLatestEnvironmentTelemetry(
  apiBaseUrl: string,
  classroomId: UUID,
): Promise<LatestEnvironmentTelemetry> {
  const response = await fetch(
    `${apiBaseUrl}/api/v1/classrooms/${classroomId}/telemetry/latest`,
    { credentials: "include", cache: "no-store" },
  );
  if (!response.ok) {
    const body = (await response.json().catch(() => null)) as
      | { detail?: unknown }
      | null;
    throw new Error(
      typeof body?.detail === "string"
        ? body.detail
        : `Live sensor request failed (${response.status})`,
    );
  }
  return (await response.json()) as LatestEnvironmentTelemetry;
}
