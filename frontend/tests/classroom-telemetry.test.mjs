import assert from "node:assert/strict";
import test from "node:test";
import { classroomTelemetryView, mergeClassroomMeterHistory } from "../src/lib/classroom-telemetry.ts";
import { accumulatedSolarEnergyMetric, energyChartHistory, estimatedEnergySavedMetric } from "../src/lib/prototype-energy.ts";

const received = "2026-11-26T04:00:00.000Z";
const now = Date.parse(received) + 1_000;
const latest = {
  message_id: "sample-1",
  received_at: received,
  occurred_at: null,
  payload: {
    boot_id: "classroom-boot-1",
    sample_interval_ms: 2_000,
    active_power_w: 0,
    energy_wh_total: 0,
    sensors: { ina219_available: true },
  },
};
const fresh = {
  broker_connected: true,
  relay: { availability: "unknown" },
  classroom_telemetry: latest,
  classroom_telemetry_freshness: "fresh",
};

test("fresh classroom telemetry can establish the controller is online", () => {
  const view = classroomTelemetryView(fresh, null, now);
  assert.equal(view.availability, "online");
  assert.equal(view.fresh, true);
  assert.equal(view.sampleAgeMs, 1_000);
});

test("a stalled stream or explicit offline status makes the last reading stale", () => {
  assert.equal(classroomTelemetryView(fresh, null, now + 8_000).fresh, false);
  const offline = { ...fresh, relay: { availability: "offline" } };
  assert.equal(classroomTelemetryView(offline, null, now).availability, "offline");
  assert.equal(classroomTelemetryView(offline, null, now).fresh, false);
  assert.equal(classroomTelemetryView({ ...fresh, broker_connected: false }, null, now).reading, latest);
  assert.equal(classroomTelemetryView(fresh, "API unreachable", now).fresh, false);
});

test("meter history merges recorded and streamed power and energy, including zero", () => {
  const current = { received_at: received, boot_id: latest.payload.boot_id, active_power_w: 0, energy_wh_total: 0 };
  const prior = { ...current, received_at: "2026-11-26T03:59:58.000Z", active_power_w: 3.5, energy_wh_total: null };
  const history = mergeClassroomMeterHistory([], { ...fresh, classroom_meter_history: [prior, current] });
  assert.deepEqual(history, [prior, current]);
  assert.equal(mergeClassroomMeterHistory(history, fresh), history);
});

test("an INA219 fault adds a chart gap and clears a carried energy total", () => {
  const faulty = {
    ...latest,
    payload: { ...latest.payload, energy_wh_total: 4.2, sensors: { ina219_available: false } },
  };
  assert.deepEqual(mergeClassroomMeterHistory([], { ...fresh, classroom_telemetry: faulty }), [
    { received_at: received, boot_id: latest.payload.boot_id, active_power_w: null, energy_wh_total: null },
  ]);
  assert.deepEqual(mergeClassroomMeterHistory([], { ...fresh, classroom_telemetry: null }), []);
});

test("legacy history supplements streamed power without overwriting it", () => {
  const older = { received_at: "2026-11-26T03:59:58.000Z", energy_wh_total: 1.25 };
  const state = { ...fresh, energy_history: [older, { received_at: received, energy_wh_total: 0 }] };
  const current = mergeClassroomMeterHistory([], fresh);
  const history = mergeClassroomMeterHistory(current, { ...state, classroom_telemetry_freshness: "stale" });
  assert.deepEqual(history, [{ ...older, active_power_w: null, boot_id: null }, current[0]]);
});

function energySnapshot(collectedWh, consumedWh) {
  return {
    ...fresh,
    solar: {
      availability: "online",
      freshness: "fresh",
      telemetry: {
        received_at: new Date(now).toISOString(),
        payload: { boot_id: "solar-boot-1", solar_power: { energy: collectedWh, power: 0.25 }, sensors: { ina219_available: true } },
      },
    },
    classroom_telemetry: { ...latest, payload: { ...latest.payload, energy_wh_total: consumedWh } },
  };
}

test("comparison charts retain both histories even when the recording times differ", () => {
  const classroom = mergeClassroomMeterHistory([], energySnapshot(0.125, 2));
  const first = { ...classroom[0], received_at: new Date(now - 241_000).toISOString() };
  const solar = energySnapshot(0.125, 2).solar.telemetry;
  const missing = { ...solar, received_at: new Date(now + 1_000).toISOString(), payload: {
    ...solar.payload, sensors: { ina219_available: false },
  } };

  const timeline = energyChartHistory([classroom[0], first], [missing, solar]);
  assert.deepEqual(timeline.map(({ meter, powerW, energyWh }) => [meter, powerW, energyWh]), [
    ["classroom", 0, 2], ["classroom", 0, 2], ["solar", 0.25, 0.125], ["solar", null, null],
  ]);
  assert.equal(timeline[0].received_at, first.received_at);
});

function savingsMetric(snapshot, error = null, clock = now) {
  return estimatedEnergySavedMetric(snapshot, classroomTelemetryView(snapshot, error, clock), received);
}

test("accumulated solar energy shows small meter totals and preserves zero after a reset", () => {
  const metric = accumulatedSolarEnergyMetric(energySnapshot(0.000012, null), null, received);
  assert.equal(metric.status, "ok");
  assert.equal(metric.display_value, "0.000012");
  assert.equal(metric.unit, "Wh");
  assert.equal(metric.updated_at, new Date(now).toISOString());
  assert.equal(accumulatedSolarEnergyMetric(energySnapshot(0, null), null, received).display_value, "0.000000");
});

test("accumulated solar energy marks stale totals and hides unavailable measurements", () => {
  const snapshot = energySnapshot(1, null);
  assert.equal(accumulatedSolarEnergyMetric(snapshot, "API unreachable", received).status, "stale");
  snapshot.solar.telemetry.payload.sensors.ina219_available = false;
  for (const missing of [null, snapshot, energySnapshot(null, null)]) {
    const metric = accumulatedSolarEnergyMetric(missing, null, received);
    assert.equal(metric.status, "unknown");
    assert.equal(metric.display_value, "--");
  }
});

test("estimated energy saved subtracts live Wh totals and preserves a shortfall or zero", () => {
  const surplus = savingsMetric(energySnapshot(5.5, 1.25));
  assert.equal(surplus.status, "ok");
  assert.equal(surplus.value, 4.25);
  assert.equal(surplus.display_value, "4.250");
  assert.equal(surplus.unit, "Wh");
  assert.equal(surplus.detail, "5.500 Wh solar − 1.250 Wh classroom");
  assert.equal(surplus.updated_at, new Date(now).toISOString());
  assert.equal(savingsMetric(energySnapshot(0, 1.25)).display_value, "-1.250");
  assert.equal(savingsMetric(energySnapshot(0, 0)).display_value, "0.000");
});

test("estimated energy saved waits for valid totals from both INA219 meters", () => {
  const snapshot = energySnapshot(10, 3);
  snapshot.classroom_telemetry.payload.sensors = { ina219_available: false };
  const solarFault = energySnapshot(10, 3);
  solarFault.solar.telemetry.payload.sensors.ina219_available = false;
  for (const missing of [null, energySnapshot(null, 3), energySnapshot(10, null), snapshot, solarFault]) {
    const metric = savingsMetric(missing);
    assert.equal(metric.status, "unknown");
    assert.equal(metric.value, null);
    assert.equal(metric.display_value, "--");
  }
});

test("estimated energy saved hides stale and offline meter readings", () => {
  const snapshot = energySnapshot(10, 3);
  for (const metric of [
    savingsMetric({ ...snapshot, solar: { ...snapshot.solar, freshness: "stale" } }),
    savingsMetric({ ...snapshot, solar: { ...snapshot.solar, availability: "offline" } }),
    savingsMetric({ ...snapshot, relay: { availability: "offline" } }),
    savingsMetric({ ...snapshot, broker_connected: false }),
    savingsMetric(snapshot, "API unreachable"),
    savingsMetric(snapshot, null, now + 8_000),
  ]) {
    assert.equal(metric.status, "stale");
    assert.equal(metric.value, null);
  }
});
