import type { OccupancyState } from "../types/contracts";

export const OCCUPANCY_FRESHNESS_MS = 15_000;
const HISTORY_MS = 60_000;

export interface OccupancySample {
  observed_at: string;
  count: number;
}

export function freshOccupancy(reading: OccupancyState | null, now: number): boolean {
  if (!reading || reading.freshness !== "fresh" || reading.status === "unknown"
    || reading.count === null || !Number.isSafeInteger(reading.count) || reading.count < 0) return false;
  const age = now - Date.parse(reading.observed_at ?? "");
  return age >= 0 && age <= OCCUPANCY_FRESHNESS_MS;
}

export function appendOccupancySample(
  history: OccupancySample[], reading: OccupancyState, now: number,
): OccupancySample[] {
  if (!freshOccupancy(reading, now)) return [];
  const sample = { observed_at: reading.observed_at!, count: reading.count! };
  const time = Date.parse(sample.observed_at);
  const previous = history.at(-1);
  const recent = history.filter((item) => now - Date.parse(item.observed_at) <= HISTORY_MS);
  if (previous && time <= Date.parse(previous.observed_at)) return recent;
  const gap = previous && time - Date.parse(previous.observed_at) > OCCUPANCY_FRESHNESS_MS;
  return [...(gap ? [] : recent), sample].slice(-64);
}

export function observedOccupancyTrend(history: OccupancySample[], now: number) {
  const recent = history.filter((sample) => {
    const age = now - Date.parse(sample.observed_at);
    return age >= 0 && age <= HISTORY_MS;
  });
  const first = recent[0];
  const last = recent.at(-1);
  if (!first || !last || now - Date.parse(last.observed_at) > OCCUPANCY_FRESHNESS_MS
    || Date.parse(last.observed_at) - Date.parse(first.observed_at) < 5000) {
    return { label: "Collecting observations", samples: [] as OccupancySample[] };
  }
  const change = last.count - first.count;
  const label = change > 0 ? "Occupancy increased" : change < 0 ? "Occupancy decreased" : "Occupancy unchanged";
  return { label, samples: recent };
}

export function occupancyInsight(
  reading: OccupancyState | null, history: OccupancySample[], now: number | null,
) {
  const fresh = now !== null && freshOccupancy(reading, now);
  const age = now === null ? NaN : now - Date.parse(reading?.observed_at ?? "");
  const ageSeconds = Number.isFinite(age) && age >= 0 ? Math.floor(age / 1000) : null;
  const trend = fresh ? observedOccupancyTrend(history, now!) : { label: "Occupancy unknown", samples: [] };
  return {
    fresh,
    count: fresh ? reading!.count : null,
    age_seconds: ageSeconds,
    freshness_pct: fresh ? Math.max(0, (1 - age / OCCUPANCY_FRESHNESS_MS) * 100) : 0,
    trend,
  };
}
