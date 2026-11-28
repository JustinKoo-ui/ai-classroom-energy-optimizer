import assert from "node:assert/strict";
import test from "node:test";
import {
  compassPoint, directionVector, normalizeBearing, pointingError, signedBearingDelta, twinState, withinTravel,
} from "../src/lib/solar-twin.ts";

const near = (actual, expected, tolerance = 1e-9) =>
  assert.ok(Math.abs(actual - expected) <= tolerance, `${actual} != ${expected}`);

function snapshot({ tracking = {}, power = {}, fresh = true, broker = true, availability = "online", telemetry = true } = {}) {
  return {
    broker_connected: broker,
    relay: { availability: "online", freshness: "fresh", last_received_at: null },
    solar: {
      availability, freshness: fresh ? "fresh" : "stale", last_received_at: "2026-11-27T04:00:00Z",
      telemetry: telemetry ? {
        message_id: "00000000-0000-4000-8000-000000000001",
        received_at: "2026-11-27T04:00:00Z", occurred_at: null,
        payload: {
          boot_id: "00000000-0000-4000-8000-000000000002",
          gps: { fix: false, time_valid: false, latitude: null, longitude: null, last_fix_age_ms: null },
          tracking: {
            mode: "auto", target_azimuth: 180, actual_azimuth: 170, target_zenith: 30, actual_zenith: 25,
            azimuth_pwm: 120, zenith_pwm: 0, stop_reason: null, ...tracking,
          },
          solar_power: { voltage: 17.5, current: 0.42, power: 7.35, energy: 1.2, ...power },
          sensors: { ina219_available: true, azimuth_mpu_available: true, zenith_mpu_available: true },
        },
      } : null,
    },
  };
}

test("bearing helpers wrap correctly", () => {
  assert.equal(normalizeBearing(-10), 350);
  assert.equal(normalizeBearing(370), 10);
  assert.equal(signedBearingDelta(350, 10), 20);
  assert.equal(signedBearingDelta(10, 350), -20);
  assert.equal(signedBearingDelta(90, 270), -180);
  assert.equal(compassPoint(180), "S");
  assert.equal(compassPoint(135), "SE");
  assert.equal(compassPoint(359), "N");
});

test("direction vectors follow compass conventions", () => {
  const east = directionVector(90, 90);
  near(east.east, 1); near(east.up, 0); near(east.north, 0);
  const southTilted = directionVector(180, 60);
  near(southTilted.north, -Math.sin(Math.PI / 3)); near(southTilted.up, 0.5);
  const up = directionVector(123, 0);
  near(up.up, 1);
});

test("pointing error is the angle between panel normal and target", () => {
  near(pointingError(180, 30, 180, 30), 0, 1e-6);
  near(pointingError(180, 30, 180, 40), 10, 1e-6);
  near(pointingError(90, 0, 270, 0), 0, 1e-6); // both straight up: azimuth irrelevant
  near(pointingError(90, 90, 270, 90), 180, 1e-6);
});

test("live telemetry produces the measured pose, target, errors and drives", () => {
  const state = twinState(snapshot(), null);
  assert.equal(state.data, "live");
  assert.equal(state.azimuthDeg, 170);
  assert.equal(state.tiltDeg, 25);
  assert.deepEqual(state.target, { azimuthDeg: 180, zenithDeg: 30, kind: "sun", aboveHorizon: true });
  assert.equal(state.azimuthErrorDeg, 10);
  assert.equal(state.tiltErrorDeg, 5);
  assert.ok(state.pointingErrorDeg > 5 && state.pointingErrorDeg < 10);
  assert.equal(state.azimuthDrive, true);
  assert.equal(state.tiltDrive, false);
  assert.equal(state.powerW, 7.35);
  assert.equal(state.fixedTilt, false);
  assert.equal(state.travelLimits, null);
});

test("stale data keeps the last pose but never claims motors or power", () => {
  for (const options of [{ fresh: false }, { broker: false }, { availability: "offline" }]) {
    const state = twinState(snapshot(options), null);
    assert.equal(state.data, "stale");
    assert.equal(state.azimuthDeg, 170);
    assert.equal(state.azimuthDrive, null);
    assert.equal(state.tiltDrive, null);
    assert.equal(state.powerW, null);
  }
  assert.equal(twinState(snapshot(), "API unreachable").data, "stale");
});

test("no telemetry means nothing is invented", () => {
  const state = twinState(snapshot({ telemetry: false }), null);
  assert.equal(state.data, "none");
  assert.equal(state.azimuthDeg, null);
  assert.equal(state.target, null);
  assert.equal(state.pointingErrorDeg, null);
  assert.equal(twinState(null, null).data, "none");
});

test("missing sensors leave their axis unknown", () => {
  const state = twinState(snapshot({ tracking: { actual_zenith: null, target_azimuth: null } }), null);
  assert.equal(state.tiltDeg, null);
  assert.equal(state.target, null);
  assert.equal(state.pointingErrorDeg, null);
  assert.equal(state.azimuthErrorDeg, null);
});

test("manual targets are labelled manual and below-horizon targets are flagged", () => {
  const manual = twinState(snapshot({ tracking: { mode: "manual" } }), null);
  assert.equal(manual.target.kind, "manual");
  const night = twinState(snapshot({ tracking: { target_zenith: 100 } }), null);
  assert.equal(night.target.aboveHorizon, false);
});

test("fixed-tilt mode is a sun target with travel limits and no tilt error", () => {
  const state = twinState(snapshot({ tracking: { mode: "manual", fixed_yaw: {
    clock_set: true, referenced: true, calibrating: false, armed: true, reachable: true,
    status: "tracking", demo_speed: 1, solar_time: null,
  } } }), null);
  assert.equal(state.fixedTilt, true);
  assert.equal(state.target.kind, "sun");
  assert.deepEqual(state.travelLimits, { minDeg: 55, maxDeg: 305 });
  assert.equal(state.tiltErrorDeg, null);
  assert.equal(withinTravel(180), true);
  assert.equal(withinTravel(10), false);
  assert.equal(withinTravel(330), false);
});
