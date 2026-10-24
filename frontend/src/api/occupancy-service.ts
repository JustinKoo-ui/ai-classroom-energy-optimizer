import type { OccupancyState, UUID } from "@/src/types/contracts";

export async function getCurrentOccupancy(
  apiBaseUrl: string,
  classroomId: UUID,
): Promise<OccupancyState> {
  const response = await fetch(
    `${apiBaseUrl}/api/v1/classrooms/${classroomId}/occupancy/current`,
    { credentials: "include", cache: "no-store" },
  );
  if (!response.ok) {
    const body = (await response.json().catch(() => null)) as
      | { detail?: unknown }
      | null;
    throw new Error(
      typeof body?.detail === "string"
        ? body.detail
        : `Live occupancy request failed (${response.status})`,
    );
  }
  return (await response.json()) as OccupancyState;
}
