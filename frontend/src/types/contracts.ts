/**
 * Canonical frontend wire contracts for REST and MQTT documentation.
 *
 * The browser never connects to MQTT. MQTT types live here so fixtures and
 * protocol documentation can be checked against one shared vocabulary.
 * Wire fields intentionally remain snake_case.
 */

export type UUID = string;
export type ISODateTime = string;
export type JsonPrimitive = string | number | boolean | null;
export type JsonValue = JsonPrimitive | JsonValue[] | JsonObject;
export interface JsonObject {
  [key: string]: JsonValue;
}

export type UserRole = "viewer" | "operator" | "admin";
export type Freshness = "fresh" | "stale" | "unknown";
export type DeviceType =
  | "air_conditioner"
  | "fan"
  | "lighting"
  | "environment_sensor"
  | "energy_meter"
  | "camera_gateway"
  | "other";
export type DeviceOperationalStatus =
  | "registered"
  | "online"
  | "offline"
  | "fault"
  | "disabled";
export type OccupancyStatus =
  | "occupied"
  | "possibly_vacant"
  | "vacant_confirmed"
  | "unknown";
export type ControlTrigger =
  | "manual"
  | "override"
  | "schedule"
  | "occupancy"
  | "vacancy"
  | "comfort"
  | "lighting"
  | "ai_recommendation"
  | "safety";
export type DecisionOutcome = "allowed" | "denied";
export type CommandAction =
  | "set_power"
  | "set_temperature"
  | "set_fan_speed"
  | "set_brightness"
  | "set_mode";
export type CommandStatus =
  | "queued"
  | "published"
  | "accepted"
  | "applied"
  | "rejected"
  | "failed"
  | "timed_out"
  | "cancelled";
export type RecommendationStatus =
  | "pending"
  | "accepted"
  | "rejected"
  | "expired"
  | "superseded";
export type OverrideMode = "force_state" | "suspend_automation";
export type OverrideStatus = "scheduled" | "active" | "cancelled" | "expired";
export type EventSeverity = "info" | "warning" | "critical";
export type EventStatus = "open" | "acknowledged" | "resolved";

export interface UserProfile {
  id: UUID;
  email: string;
  display_name: string;
  role: UserRole;
}

export interface Site {
  id: UUID;
  code: string;
  name: string;
  timezone: string;
  is_active: boolean;
  created_at: ISODateTime;
  updated_at: ISODateTime;
}

export interface Classroom {
  id: UUID;
  site_id: UUID;
  code: string;
  name: string;
  capacity: number;
  floor: string | null;
  timezone: string;
  control_policy_version: string;
  is_active: boolean;
  created_at: ISODateTime;
  updated_at: ISODateTime;
}

export interface DeviceCapabilities {
  supported_actions: CommandAction[];
  supports_power: boolean;
  setpoint_min_c: number | null;
  setpoint_max_c: number | null;
  fan_speed_steps_pct: number[];
  brightness_steps_pct: number[];
  supported_modes: string[];
}

export interface Device {
  id: UUID;
  classroom_id: UUID;
  hardware_uid: string;
  name: string;
  type: DeviceType;
  rated_power_w: number | null;
  capabilities: DeviceCapabilities;
  enabled: boolean;
  created_at: ISODateTime;
  updated_at: ISODateTime;
}

export interface DeviceState {
  device_id: UUID;
  operational_status: DeviceOperationalStatus;
  desired_state: JsonObject;
  reported_state: JsonObject;
  fault_codes: string[];
  state_version: number;
  last_seen_at: ISODateTime | null;
  reported_at: ISODateTime | null;
  freshness: Freshness;
  updated_at: ISODateTime;
}

export interface OccupancyState {
  status: OccupancyStatus;
  count: number | null;
  confidence: number | null;
  observed_at: ISODateTime | null;
  freshness: Freshness;
}

export interface EnvironmentState {
  temperature_c: number;
  relative_humidity_pct: number;
  illuminance_lux: number;
  observed_at: ISODateTime;
  freshness: Freshness;
}

export interface DeparturePrediction {
  id: UUID;
  classroom_id: UUID;
  horizon_seconds: 60;
  leave_probability: number;
  model_version: string;
  feature_set_version: string;
  predicted_at: ISODateTime;
  expires_at: ISODateTime;
}

export interface AIRecommendation {
  id: UUID;
  classroom_id: UUID;
  device_id: UUID | null;
  prediction_id: UUID | null;
  recommended_action: CommandAction;
  recommended_value: JsonObject;
  reason: string;
  confidence: number | null;
  status: RecommendationStatus;
  created_at: ISODateTime;
  expires_at: ISODateTime;
}

export interface ControlDecisionSummary {
  id: UUID;
  request_id: UUID;
  outcome: DecisionOutcome;
  reason_codes: string[];
  summary: string;
  matched_rule: string;
  bounded_action: CommandAction | null;
  bounded_value: JsonObject | null;
  policy_version: string;
  evaluated_at: ISODateTime;
}

export interface DeviceCommand {
  id: UUID;
  classroom_id: UUID;
  device_id: UUID;
  decision_id: UUID;
  action: CommandAction;
  value: JsonObject;
  status: CommandStatus;
  retry_count: number;
  created_at: ISODateTime;
  updated_at: ISODateTime;
  expires_at: ISODateTime;
}

export interface ManualOverride {
  id: UUID;
  classroom_id: UUID;
  device_id: UUID;
  created_by: UUID;
  mode: OverrideMode;
  value: JsonObject | null;
  reason: string;
  status: OverrideStatus;
  starts_at: ISODateTime;
  ends_at: ISODateTime;
  created_at: ISODateTime;
  updated_at: ISODateTime;
}

export interface Schedule {
  id: UUID;
  classroom_id: UUID;
  device_id: UUID;
  name: string;
  action: CommandAction;
  value: JsonObject;
  rrule: string;
  timezone: string;
  starts_at: ISODateTime;
  ends_at: ISODateTime | null;
  max_lateness_seconds: number;
  enabled: boolean;
  version: number;
  next_run_at: ISODateTime | null;
  created_at: ISODateTime;
  updated_at: ISODateTime;
}

export interface EnergyUpdate {
  classroom_id: UUID;
  device_id: UUID | null;
  active_power_w: number;
  period_energy_wh: number;
  period_started_at: ISODateTime;
  observed_at: ISODateTime;
  freshness: Freshness;
}

export interface SystemEvent {
  id: UUID;
  classroom_id: UUID | null;
  device_id: UUID | null;
  severity: EventSeverity;
  status: EventStatus;
  code: string;
  message: string;
  details: JsonObject;
  occurred_at: ISODateTime;
}

export interface ClassroomSnapshot {
  classroom_id: UUID;
  occupancy: OccupancyState;
  environment: EnvironmentState | null;
  devices: DeviceState[];
  latest_prediction: DeparturePrediction | null;
  active_overrides: ManualOverride[];
  computed_at: ISODateTime;
}

// REST

export interface Page<T> {
  items: T[];
  next_cursor: string | null;
}

export interface ApiErrorResponse {
  error: {
    code: string;
    message: string;
    details: JsonObject;
    trace_id: UUID;
  };
}

export interface ManualCommandRequest {
  action: CommandAction;
  value: JsonObject;
  reason: string;
  expires_in_seconds: number;
}

export interface SiteCreate {
  code: string;
  name: string;
  timezone: string;
}

export interface SiteUpdate {
  name?: string;
  timezone?: string;
  is_active?: boolean;
}

export interface ClassroomCreate {
  site_id: UUID;
  code: string;
  name: string;
  capacity: number;
  floor: string | null;
  timezone: string;
  control_policy_id: UUID;
}

export interface ClassroomUpdate {
  name?: string;
  capacity?: number;
  floor?: string | null;
  timezone?: string;
  is_active?: boolean;
}

export interface DeviceCreate {
  hardware_uid: string;
  mqtt_client_id: string;
  name: string;
  type: DeviceType;
  rated_power_w: number | null;
  capabilities: DeviceCapabilities;
}

export interface DeviceUpdate {
  name?: string;
  rated_power_w?: number | null;
  capabilities?: DeviceCapabilities;
  enabled?: boolean;
}

export interface ManualCommandSubmission {
  request_id: UUID;
  decision_id: UUID;
  decision: DecisionOutcome;
  reason_codes: string[];
  command_id: UUID | null;
  command_status: CommandStatus | null;
}

export interface ManualOverrideCreate {
  mode: OverrideMode;
  value: JsonObject | null;
  starts_at: ISODateTime;
  ends_at: ISODateTime;
  reason: string;
}

export interface ScheduleCreate {
  device_id: UUID;
  name: string;
  action: CommandAction;
  value: JsonObject;
  rrule: string;
  timezone: string;
  starts_at: ISODateTime;
  ends_at: ISODateTime | null;
  max_lateness_seconds: number;
  enabled: boolean;
}

export interface ScheduleUpdate {
  expected_version: number;
  name?: string;
  action?: CommandAction;
  value?: JsonObject;
  rrule?: string;
  timezone?: string;
  starts_at?: ISODateTime;
  ends_at?: ISODateTime | null;
  max_lateness_seconds?: number;
  enabled?: boolean;
}

export interface EnergySummary {
  classroom_id: UUID;
  period_start: ISODateTime;
  period_end: ISODateTime;
  energy_wh: number;
  average_power_w: number;
  peak_power_w: number;
  estimated_cost: number | null;
  currency: string | null;
  by_device: Record<UUID, number>;
}

export interface EnergySavingsEstimate {
  id: UUID;
  classroom_id: UUID;
  device_id: UUID | null;
  period_start: ISODateTime;
  period_end: ISODateTime;
  baseline_method: string;
  baseline_version: string;
  baseline_energy_wh: number;
  actual_energy_wh: number;
  estimated_savings_wh: number;
  estimated_cost_savings: number | null;
  currency: string | null;
  quality_score: number | null;
  methodology_notes: string | null;
  calculated_at: ISODateTime;
}

// MQTT (for shared fixtures and documentation; never instantiate in browser UI)

export interface MqttEnvelope<TPayload> {
  schema_version: `1.${number}`;
  message_id: UUID;
  occurred_at: ISODateTime;
  site_id: UUID;
  classroom_id: UUID;
  producer_id: string;
  payload: TPayload;
}

export interface MqttEnvelopeV2<TPayload> {
  schema_version: `2.${number}`;
  message_id: UUID;
  occurred_at: ISODateTime;
  site_id: UUID;
  classroom_id: UUID;
  producer_id: string;
  payload: TPayload;
}

export interface EnvironmentTelemetryPayload {
  sensor_id: string;
  temperature_c: number;
  relative_humidity_pct: number;
  illuminance_lux: number;
  sample_interval_ms: number;
}

export interface EnvironmentTelemetryPayloadV2 {
  sensor_id: string;
  temperature_c: number;
  relative_humidity_pct: number;
  illuminance_adc: number;
  motion_detected: boolean | null;
  sample_interval_ms: number;
}

export interface PowerTelemetryPayload {
  device_id: UUID;
  voltage_v: number | null;
  current_a: number | null;
  active_power_w: number;
  apparent_power_va: number | null;
  power_factor: number | null;
  energy_wh_total: number | null;
  sample_interval_ms: number;
}

export interface OccupancyObservationPayload {
  camera_id: string;
  count: number;
  confidence: number;
  entries_delta: number;
  exits_delta: number;
  frame_sequence: number;
  source: "roboflow_bytetrack";
}

export interface AvailabilityPayload {
  status: "online" | "offline";
  boot_id: UUID;
  firmware_version: string;
}

export interface DeviceStatePayload {
  device_id: UUID;
  state: JsonObject;
  fault_codes: string[];
  state_version: number;
}

export interface ApprovedCommandPayload {
  command_id: UUID;
  decision_id: UUID;
  device_id: UUID;
  action: CommandAction;
  value: JsonObject;
  expires_at: ISODateTime;
  idempotency_key: string;
}

export interface CommandAcknowledgementPayload {
  command_id: UUID;
  device_id: UUID;
  status: "accepted" | "applied" | "rejected" | "failed";
  reason_code: string | null;
  reported_state: JsonObject | null;
  applied_at: ISODateTime | null;
}
