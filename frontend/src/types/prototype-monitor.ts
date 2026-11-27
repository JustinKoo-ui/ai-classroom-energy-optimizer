import type { Freshness, ISODateTime, UUID } from "@/src/types/contracts";

export interface PrototypeConnection {
  availability: "online" | "offline" | "unknown";
  freshness: Freshness;
  last_received_at: ISODateTime | null;
}

export interface SolarYawSequence {
  kind: "test" | "run";
  phase: "idle" | "clockwise" | "pause" | "anticlockwise";
  leg_progress_deg: number | null;
  leg_target_deg: number | null;
  net_rotation_deg: number;
  result: "completed" | "user_stopped" | "feedback_unavailable" | "sampling_interrupted"
    | "wrong_direction" | "stalled" | "timed_out" | null;
}

export interface SolarFixedYaw {
  clock_set: boolean;
  referenced: boolean;
  calibrating: boolean;
  armed: boolean;
  reachable: boolean;
  status: "waiting_for_clock" | "waiting_for_reference" | "calibrating" | "ready" | "tracking" | "aligned"
    | "sun_below_horizon" | "outside_travel" | "feedback_unavailable" | "reference_expired"
    | "base_moved_during_calibration" | "sampling_interrupted" | "control_delayed"
    | "wrong_direction" | "stalled";
  demo_speed: number;
  solar_time: ISODateTime | null;
}

export interface SolarTelemetry {
  message_id: UUID;
  received_at: ISODateTime;
  occurred_at: ISODateTime | null;
  payload: {
    boot_id: UUID;
    gps: {
      enabled?: boolean;
      fix: boolean;
      time_valid: boolean;
      latitude: number | null;
      longitude: number | null;
      last_fix_age_ms: number | null;
    };
    tracking: {
      mode: "auto" | "manual";
      target_azimuth: number | null;
      actual_azimuth: number | null;
      target_zenith: number | null;
      actual_zenith: number | null;
      azimuth_pwm: number;
      zenith_pwm: number;
      stop_reason: "waiting_for_gps" | "waiting_for_clock" | "spa_error" | "feedback_unavailable" | "zenith_limit"
        | "local_hold" | null;
      yaw_sequence?: SolarYawSequence | null;
      fixed_yaw?: SolarFixedYaw | null;
    };
    solar_power: {
      voltage: number | null;
      current: number | null;
      power: number | null;
      energy: number | null;
    };
    sensors: {
      ina219_available: boolean;
      azimuth_mpu_available: boolean;
      zenith_mpu_available: boolean;
    };
  };
}

export interface ClassroomTelemetry {
  message_id: UUID;
  received_at: ISODateTime;
  occurred_at: ISODateTime | null;
  payload: {
    sensor_id: string;
    boot_id: UUID;
    uptime_ms: number;
    sample_interval_ms: number;
    temperature_c: number | null;
    relative_humidity_pct: number | null;
    illuminance_lux: number | null;
    motion_detected: boolean | null;
    voltage_v: number | null;
    current_a: number | null;
    active_power_w: number | null;
    energy_wh_total: number | null;
    energy_kwh_total: number | null;
    relays: {
      light: boolean;
      fan: boolean;
      air_conditioner: boolean;
    };
    sensors: {
      dht22_available: boolean;
      pir_ready: boolean;
      bh1750_available: boolean;
      bh1750_fresh: boolean;
      bh1750_age_ms: number | null;
      ina219_available: boolean;
    };
    energy_storage_available: boolean;
  };
}

export interface ClassroomEnergyPoint {
  received_at: ISODateTime;
  energy_wh_total: number;
}

export interface ClassroomMeterPoint {
  received_at: ISODateTime;
  boot_id: UUID | null;
  active_power_w: number | null;
  energy_wh_total: number | null;
}

export interface PrototypeMonitorSnapshot {
  broker_connected: boolean;
  solar: PrototypeConnection & { telemetry: SolarTelemetry | null };
  relay: PrototypeConnection;
  history?: SolarTelemetry[];
  classroom_telemetry?: ClassroomTelemetry | null;
  classroom_telemetry_freshness?: Freshness;
  energy_history?: ClassroomEnergyPoint[] | null;
  classroom_meter_history?: ClassroomMeterPoint[] | null;
}
