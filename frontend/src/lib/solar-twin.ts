import type { PrototypeMonitorSnapshot, SolarTelemetry } from "../types/prototype-monitor";

/**
 * Maps solar tracker telemetry onto the digital twin.
 *
 * Conventions (docs/solar-tracker.md): azimuth is a compass bearing, 0° = north,
 * clockwise (b90 = true east, true south = 180°). Zenith is measured from straight
 * up, so a panel whose normal has zenith Z is tilted Z degrees from horizontal.
 * The twin draws only reported values: unknown stays unknown, and stale data is
 * shown as the last known pose, never as live motion.
 */

/** Fixed-tilt yaw travel from the firmware: bearings 55°–305°, never through north. */
export const FIXED_YAW_TRAVEL = { minDeg: 55, maxDeg: 305 } as const;

export type TwinDataState = "live" | "stale" | "none";

export interface TwinTarget {
  azimuthDeg: number;
  zenithDeg: number;
  /** "sun" when calculated from the solar position; "manual" for a typed MANUAL target. */
  kind: "sun" | "manual";
  aboveHorizon: boolean;
}

export interface TwinState {
  data: TwinDataState;
  receivedAt: string | null;
  /** Measured panel bearing; null when the azimuth sensor gave no value. */
  azimuthDeg: number | null;
  /** Measured panel tilt from horizontal; null when the tilt sensor gave no value. */
  tiltDeg: number | null;
  target: TwinTarget | null;
  /** Angle between where the panel faces and the target; null if either is unknown. */
  pointingErrorDeg: number | null;
  /** Signed shortest turn from actual to target azimuth (+ = clockwise). */
  azimuthErrorDeg: number | null;
  tiltErrorDeg: number | null;
  /** Motor drive requested (PWM > 0) on live data only; duty is not movement feedback. */
  azimuthDrive: boolean | null;
  tiltDrive: boolean | null;
  powerW: number | null;
  voltageV: number | null;
  currentA: number | null;
  fixedTilt: boolean;
  travelLimits: typeof FIXED_YAW_TRAVEL | null;
}

const finite = (value: number | null | undefined): value is number =>
  typeof value === "number" && Number.isFinite(value);

export function normalizeBearing(deg: number): number {
  return ((deg % 360) + 360) % 360;
}

/** Shortest signed difference to - from in degrees, in [-180, 180). */
export function signedBearingDelta(from: number, to: number): number {
  return ((to - from + 540) % 360) - 180;
}

/** Unit vector {east, up, north} for a direction given by compass azimuth and zenith. */
export function directionVector(azimuthDeg: number, zenithDeg: number) {
  const a = (azimuthDeg * Math.PI) / 180;
  const z = (zenithDeg * Math.PI) / 180;
  return { east: Math.sin(z) * Math.sin(a), up: Math.cos(z), north: Math.sin(z) * Math.cos(a) };
}

/** Angle in degrees between the panel normal and a target direction. */
export function pointingError(
  panelAzimuthDeg: number, panelTiltDeg: number, targetAzimuthDeg: number, targetZenithDeg: number,
): number {
  const p = directionVector(panelAzimuthDeg, panelTiltDeg);
  const t = directionVector(targetAzimuthDeg, targetZenithDeg);
  const dot = Math.min(1, Math.max(-1, p.east * t.east + p.up * t.up + p.north * t.north));
  return (Math.acos(dot) * 180) / Math.PI;
}

/** Same freshness rule as the Solar tracker page (lib/prototype-energy). */
function isLive(snapshot: PrototypeMonitorSnapshot | null, error: string | null): boolean {
  const solar = snapshot?.solar;
  return !error && snapshot?.broker_connected === true && !!solar?.telemetry &&
    solar.freshness === "fresh" && solar.availability !== "offline";
}

function targetOf(tracking: SolarTelemetry["payload"]["tracking"]): TwinTarget | null {
  if (!finite(tracking.target_azimuth) || !finite(tracking.target_zenith)) return null;
  const kind = tracking.fixed_yaw || tracking.mode === "auto" ? "sun" : "manual";
  return {
    azimuthDeg: normalizeBearing(tracking.target_azimuth),
    zenithDeg: tracking.target_zenith,
    kind,
    aboveHorizon: tracking.target_zenith < 90,
  };
}

export function twinState(snapshot: PrototypeMonitorSnapshot | null, error: string | null): TwinState {
  const sample = snapshot?.solar.telemetry ?? null;
  const data: TwinDataState = !sample ? "none" : isLive(snapshot, error) ? "live" : "stale";
  const tracking = sample?.payload.tracking;
  const power = sample?.payload.solar_power;
  const azimuthDeg = finite(tracking?.actual_azimuth) ? normalizeBearing(tracking.actual_azimuth) : null;
  const tiltDeg = finite(tracking?.actual_zenith) ? tracking.actual_zenith : null;
  const target = tracking ? targetOf(tracking) : null;
  const fixedTilt = !!tracking?.fixed_yaw;
  const live = data === "live";

  return {
    data,
    receivedAt: sample?.received_at ?? null,
    azimuthDeg,
    tiltDeg,
    target,
    pointingErrorDeg: azimuthDeg !== null && tiltDeg !== null && target
      ? pointingError(azimuthDeg, tiltDeg, target.azimuthDeg, target.zenithDeg)
      : null,
    azimuthErrorDeg: azimuthDeg !== null && target ? signedBearingDelta(azimuthDeg, target.azimuthDeg) : null,
    tiltErrorDeg: tiltDeg !== null && target && !fixedTilt ? target.zenithDeg - tiltDeg : null,
    azimuthDrive: live && tracking ? tracking.azimuth_pwm > 0 : null,
    tiltDrive: live && tracking ? tracking.zenith_pwm > 0 : null,
    powerW: live && finite(power?.power) ? power.power : null,
    voltageV: live && finite(power?.voltage) ? power.voltage : null,
    currentA: live && finite(power?.current) ? power.current : null,
    fixedTilt,
    travelLimits: fixedTilt ? FIXED_YAW_TRAVEL : null,
  };
}

/** True when a bearing lies inside the fixed-tilt travel range. */
export function withinTravel(bearingDeg: number, limits = FIXED_YAW_TRAVEL): boolean {
  const b = normalizeBearing(bearingDeg);
  return b >= limits.minDeg && b <= limits.maxDeg;
}

/** Compass name for a bearing, e.g. 180 → "S", 135 → "SE". */
export function compassPoint(bearingDeg: number): string {
  const points = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"];
  return points[Math.round(normalizeBearing(bearingDeg) / 45) % 8];
}
