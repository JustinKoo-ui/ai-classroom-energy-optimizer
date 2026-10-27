import assert from "node:assert/strict";
import test from "node:test";
import { energyPresentation, runtimeCoverage } from "../src/lib/analytics-presentation.ts";

const period = {
  period_start: "2026-10-25T16:00:00Z",
  period_end: "2026-10-26T16:00:00Z",
  as_of: "2026-10-26T16:00:00Z",
};

function device({
  key = "lighting",
  on = 1800,
  off = 1800,
  unknown = 0,
  tracking = period.period_start,
} = {}) {
  return {
    key,
    tracking_started_at: tracking,
    runtime: {
      on_seconds: on,
      off_seconds: off,
      unknown_seconds: unknown,
      elapsed_seconds: on + off + unknown,
    },
  };
}

function energy(overrides = {}) {
  return {
    estimated_energy_kwh: 0.1,
    daily_24h_baseline_kwh: 0.24,
    period_baseline_kwh: 0.24,
    theoretical_difference_kwh: 0.14,
    provisional_difference_pct: 58.3333333333,
    final_savings_pct: 58.3333333333,
    is_complete: true,
    ...overrides,
  };
}

test("combined coverage weights recorded time by elapsed device-seconds", () => {
  const coverage = runtimeCoverage([
    device({ on: 25, off: 25, unknown: 50 }),
    device({ key: "fan", on: 75, off: 225, unknown: 0 }),
  ]);
  assert.equal(coverage.coverage_pct, 87.5);
  assert.equal(coverage.elapsed_seconds, 400);
  assert.equal(coverage.known_seconds, 350);
  assert.equal(coverage.unknown_seconds, 50);
});

test("incomplete coverage hides supplied high provisional and final percentages", () => {
  const result = energyPresentation(period, [device({ on: 169, off: 0, unknown: 3431 })],
    energy({ is_complete: false, provisional_difference_pct: 99, final_savings_pct: 99 }));
  assert.ok(Math.abs(result.coverage_pct - 4.6944444444) < 0.000001);
  assert.equal(result.provisional_pct, null);
  assert.equal(result.final_pct, null);
  assert.deepEqual(result.reasons, ["Incomplete runtime coverage"]);
});

test("an unfinished day with complete elapsed coverage still withholds percentages", () => {
  const result = energyPresentation(
    { ...period, as_of: "2026-10-26T04:00:00Z" },
    [device()],
    energy({ is_complete: false, provisional_difference_pct: 99, final_savings_pct: null }),
  );
  assert.equal(result.coverage_pct, 100);
  assert.equal(result.provisional_pct, null);
  assert.equal(result.final_pct, null);
  assert.deepEqual(result.reasons, ["Reporting period still in progress"]);
});

test("a completed period with full coverage displays its calculated percentages", () => {
  const result = energyPresentation(period, [device()], energy());
  assert.equal(result.coverage_pct, 100);
  assert.equal(result.provisional_pct, 58.3333333333);
  assert.equal(result.final_pct, 58.3333333333);
  assert.deepEqual(result.reasons, []);
});

test("zero and negative complete comparison percentages are preserved", () => {
  for (const percentage of [0, -4.1666666667]) {
    const result = energyPresentation(period, [device()], energy({
      provisional_difference_pct: percentage,
      final_savings_pct: percentage,
    }));
    assert.equal(result.provisional_pct, percentage);
    assert.equal(result.final_pct, percentage);
    assert.deepEqual(result.reasons, []);
  }
});

test("missing runtime history explains why zero estimated usage has no comparison", () => {
  const result = energyPresentation(period,
    [device({ on: 0, off: 0, unknown: 86400, tracking: null })],
    energy({ is_complete: false, estimated_energy_kwh: 0, final_savings_pct: null }));
  assert.equal(result.coverage_pct, 0);
  assert.equal(result.final_pct, null);
  assert.deepEqual(result.reasons, ["No runtime observations recorded"]);
});

test("late tracking explains missing coverage even after the reporting day ends", () => {
  const result = energyPresentation(period,
    [device({ on: 600, off: 0, unknown: 85800, tracking: "2026-10-26T15:50:00Z" })],
    energy({ is_complete: false, final_savings_pct: null }));
  assert.deepEqual(result.reasons, [
    "Tracking started partway through the period",
    "Incomplete runtime coverage",
  ]);
  assert.equal(result.provisional_pct, null);
});

test("one complete device can show its result while combined coverage is incomplete", () => {
  const light = device();
  const fan = device({ key: "fan", on: 0, off: 0, unknown: 3600, tracking: null });
  const individual = energyPresentation(period, [light], energy());
  const combined = energyPresentation(period, [light, fan], energy({ is_complete: false }));
  assert.equal(individual.final_pct, 58.3333333333);
  assert.equal(combined.coverage_pct, 50);
  assert.equal(combined.final_pct, null);
  assert.deepEqual(combined.reasons, ["Incomplete runtime coverage"]);
});

test("combined completeness applies the backend's UNKNOWN tolerance per device", () => {
  const devices = ["lighting", "fan", "air_conditioner"].map((key) => device({
    key,
    on: 3599.9995,
    off: 0,
    unknown: 0.0005,
  }));
  const result = energyPresentation(period, devices, energy());
  assert.equal(result.unknown_seconds, 0.0015);
  assert.equal(result.provisional_pct, 58.3333333333);
  assert.equal(result.final_pct, 58.3333333333);
  assert.deepEqual(result.reasons, []);
});

test("a zero baseline gives a specific reason and no division-based percentage", () => {
  const result = energyPresentation(period, [device()], energy({
    period_baseline_kwh: 0,
    provisional_difference_pct: null,
    final_savings_pct: null,
  }));
  assert.equal(result.coverage_pct, 100);
  assert.equal(result.provisional_pct, null);
  assert.equal(result.final_pct, null);
  assert.deepEqual(result.reasons, ["No positive baseline configured"]);
});

test("a future period with no elapsed time shows no invented coverage percentage", () => {
  const result = energyPresentation(
    { ...period, as_of: "2026-10-25T15:00:00Z" },
    [device({ on: 0, off: 0, unknown: 0, tracking: null })],
    energy({ is_complete: false, final_savings_pct: null }),
  );
  assert.equal(result.coverage_pct, null);
  assert.equal(result.provisional_pct, null);
  assert.deepEqual(result.reasons, [
    "Reporting period has not started",
    "No runtime observations recorded",
  ]);
});

test("an empty device list cannot claim complete coverage", () => {
  const result = energyPresentation(period, [], energy());
  assert.equal(result.coverage_pct, null);
  assert.equal(result.final_pct, null);
  assert.deepEqual(result.reasons, ["No runtime observations recorded"]);
});

test("server completeness remains required even when displayed coverage rounds to 100%", () => {
  const result = energyPresentation(period,
    [device({ on: 3599.9999, off: 0, unknown: 0.0001 })],
    energy({ is_complete: false, final_savings_pct: null }));
  assert.ok(result.coverage_pct > 99.9999);
  assert.equal(result.provisional_pct, null);
  assert.equal(result.final_pct, null);
  assert.deepEqual(result.reasons, ["Incomplete runtime coverage"]);
});
