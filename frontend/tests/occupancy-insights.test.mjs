import assert from "node:assert/strict";
import test from "node:test";
import { appendOccupancySample, freshOccupancy, observedOccupancyTrend, occupancyInsight } from "../src/lib/occupancy-insights.ts";

const start = Date.parse("2026-10-27T04:00:00Z");
const at = (seconds) => new Date(start + seconds * 1000).toISOString();
const reading = (seconds, count, overrides = {}) => ({
  observed_at: at(seconds), count, freshness: "fresh",
  status: count > 0 ? "occupied" : "possibly_vacant", confidence: 0.97, ...overrides,
});

test("fresh counts including zero come from the camera, without demo fallback", () => {
  for (const count of [0, 1, 3, 18]) {
    const view = occupancyInsight(reading(0, count), [], start);
    assert.equal(view.count, count);
    assert.equal(view.fresh, true);
    assert.equal(view.age_seconds, 0);
    assert.equal(view.trend.label, "Collecting observations");
  }
  assert.equal(occupancyInsight(null, [], start).count, null);
});

test("counts expire locally after 15 seconds even without a new API response", () => {
  const observation = reading(0, 3);
  assert.equal(freshOccupancy(observation, start + 15000), true);
  const stale = occupancyInsight(observation, [], start + 15001);
  assert.equal(stale.count, null);
  assert.equal(stale.fresh, false);
  assert.equal(stale.freshness_pct, 0);
  assert.equal(stale.trend.label, "Occupancy unknown");
});

test("unknown, stale, future, invalid and missing observations never establish occupancy", () => {
  for (const observation of [
    null, reading(0, null), reading(0, 3, { status: "unknown" }),
    reading(0, 3, { freshness: "stale" }), reading(0, 0, { freshness: "unknown" }),
    reading(1, 3), reading(0, 3, { observed_at: null }),
    reading(0, 3, { observed_at: "invalid" }), reading(0, -1), reading(0, 0.5),
  ]) {
    assert.equal(freshOccupancy(observation, start), false);
    assert.equal(occupancyInsight(observation, [], start).count, null);
  }
  assert.equal(occupancyInsight(reading(0, 3), [], null).count, null);
});

test("freshness ring represents sample age, not a synthetic departure percentage", () => {
  const view = occupancyInsight(reading(0, 3), [], start + 7500);
  assert.equal(view.freshness_pct, 50);
  assert.equal(view.age_seconds, 7);
  assert.equal("departure_probability" in view, false);
  assert.equal("prediction_confidence" in view, false);
});

test("observed trends require time-separated observations, not repeated polling of one frame", () => {
  let history = appendOccupancySample([], reading(0, 1), start);
  history = appendOccupancySample(history, reading(0, 1), start + 5000);
  assert.equal(history.length, 1);
  assert.equal(observedOccupancyTrend(history, start + 5000).label, "Collecting observations");
  history = appendOccupancySample(history, reading(5, 3), start + 5000);
  assert.equal(observedOccupancyTrend(history, start + 5000).label, "Occupancy increased");
  history = appendOccupancySample(history, reading(10, 0), start + 10000);
  assert.equal(observedOccupancyTrend(history, start + 10000).label, "Occupancy decreased");
  history = appendOccupancySample(history, reading(15, 1), start + 15000);
  assert.equal(observedOccupancyTrend(history, start + 15000).label, "Occupancy unchanged");
});

test("older frames cannot overwrite trend history and gaps start a new trend", () => {
  let history = appendOccupancySample([], reading(10, 2), start + 10000);
  assert.deepEqual(appendOccupancySample(history, reading(5, 8), start + 10000), history);
  history = appendOccupancySample(history, reading(26, 3), start + 26000);
  assert.deepEqual(history.map((sample) => sample.count), [3]);
  assert.equal(observedOccupancyTrend(history, start + 26000).label, "Collecting observations");
  assert.deepEqual(appendOccupancySample(history, reading(30, null, { freshness: "stale" }), start + 30000), []);
});

test("session history is bounded and stops showing a trend during an outage", () => {
  let history = [];
  for (let seconds = 0; seconds <= 120; seconds++) {
    history = appendOccupancySample(history, reading(seconds, seconds % 4), start + seconds * 1000);
  }
  assert.equal(history.length, 61);
  assert.equal(history[0].observed_at, at(60));
  assert.equal(observedOccupancyTrend(history, start + 136000).label, "Collecting observations");
  assert.equal(occupancyInsight(reading(120, 3), history, start + 136000).trend.samples.length, 0);
});
