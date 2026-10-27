-- AI Classroom Energy Optimizer
-- Initial PostgreSQL 15+ schema. Run on a new database; use Alembic migrations
-- for every subsequent change.

BEGIN;

CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TYPE user_role AS ENUM ('viewer', 'operator', 'admin');
CREATE TYPE device_type AS ENUM (
    'air_conditioner', 'fan', 'lighting', 'environment_sensor',
    'energy_meter', 'camera_gateway', 'other'
);
CREATE TYPE device_operational_status AS ENUM (
    'registered', 'online', 'offline', 'fault', 'disabled'
);
CREATE TYPE occupancy_status AS ENUM (
    'occupied', 'possibly_vacant', 'vacant_confirmed', 'unknown'
);
CREATE TYPE control_trigger AS ENUM (
    'manual', 'override', 'schedule', 'occupancy', 'vacancy', 'comfort',
    'lighting', 'ai_recommendation', 'safety'
);
CREATE TYPE decision_outcome AS ENUM ('allowed', 'denied');
CREATE TYPE command_action AS ENUM (
    'set_power', 'set_temperature', 'set_fan_speed', 'set_brightness',
    'set_mode'
);
CREATE TYPE command_status AS ENUM (
    'queued', 'published', 'accepted', 'applied', 'rejected', 'failed',
    'timed_out', 'cancelled'
);
CREATE TYPE recommendation_status AS ENUM (
    'pending', 'accepted', 'rejected', 'expired', 'superseded'
);
CREATE TYPE override_mode AS ENUM ('force_state', 'suspend_automation');
CREATE TYPE override_status AS ENUM ('scheduled', 'active', 'cancelled', 'expired');
CREATE TYPE schedule_run_status AS ENUM (
    'due', 'allowed', 'denied', 'skipped', 'completed', 'failed'
);
CREATE TYPE event_severity AS ENUM ('info', 'warning', 'critical');
CREATE TYPE event_status AS ENUM ('open', 'acknowledged', 'resolved');

CREATE TABLE app_users (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    external_subject text NOT NULL UNIQUE,
    email text NOT NULL,
    display_name text NOT NULL,
    is_active boolean NOT NULL DEFAULT true,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX uq_app_users_email_lower ON app_users (lower(email));

CREATE TABLE control_policy_versions (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    version text NOT NULL UNIQUE,
    description text NOT NULL,
    config jsonb NOT NULL,
    config_sha256 text NOT NULL,
    created_by uuid REFERENCES app_users(id) ON DELETE SET NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK (jsonb_typeof(config) = 'object')
);

CREATE TABLE sites (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    code text NOT NULL UNIQUE,
    name text NOT NULL,
    timezone text NOT NULL DEFAULT 'Asia/Kuala_Lumpur',
    is_active boolean NOT NULL DEFAULT true,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE site_memberships (
    site_id uuid NOT NULL REFERENCES sites(id) ON DELETE CASCADE,
    user_id uuid NOT NULL REFERENCES app_users(id) ON DELETE CASCADE,
    role user_role NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (site_id, user_id)
);

CREATE TABLE classrooms (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    site_id uuid NOT NULL REFERENCES sites(id) ON DELETE RESTRICT,
    code text NOT NULL,
    name text NOT NULL,
    capacity integer NOT NULL CHECK (capacity > 0),
    floor text,
    timezone text NOT NULL,
    control_policy_id uuid NOT NULL REFERENCES control_policy_versions(id) ON DELETE RESTRICT,
    is_active boolean NOT NULL DEFAULT true,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (site_id, code)
);

CREATE TABLE devices (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    classroom_id uuid NOT NULL REFERENCES classrooms(id) ON DELETE RESTRICT,
    hardware_uid text NOT NULL UNIQUE,
    mqtt_client_id text NOT NULL UNIQUE,
    name text NOT NULL,
    type device_type NOT NULL,
    manufacturer text,
    model text,
    firmware_version text,
    rated_power_w numeric(12, 3) CHECK (rated_power_w IS NULL OR rated_power_w >= 0),
    capabilities jsonb NOT NULL DEFAULT '{}'::jsonb,
    enabled boolean NOT NULL DEFAULT true,
    installed_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CHECK (jsonb_typeof(capabilities) = 'object'),
    UNIQUE (classroom_id, name)
);

CREATE INDEX ix_devices_classroom_type ON devices (classroom_id, type);

-- Current reported state is separate from desired state. The UI must not treat
-- a desired change as proof that a physical action occurred.
CREATE TABLE device_states (
    device_id uuid PRIMARY KEY REFERENCES devices(id) ON DELETE CASCADE,
    operational_status device_operational_status NOT NULL DEFAULT 'registered',
    desired_state jsonb NOT NULL DEFAULT '{}'::jsonb,
    reported_state jsonb NOT NULL DEFAULT '{}'::jsonb,
    fault_codes text[] NOT NULL DEFAULT '{}',
    state_version bigint NOT NULL DEFAULT 0 CHECK (state_version >= 0),
    last_seen_at timestamptz,
    reported_at timestamptz,
    updated_at timestamptz NOT NULL DEFAULT now(),
    CHECK (jsonb_typeof(desired_state) = 'object'),
    CHECK (jsonb_typeof(reported_state) = 'object')
);

CREATE TABLE device_state_history (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    device_id uuid NOT NULL REFERENCES devices(id) ON DELETE RESTRICT,
    message_id uuid,
    operational_status device_operational_status NOT NULL,
    reported_state jsonb NOT NULL DEFAULT '{}'::jsonb,
    fault_codes text[] NOT NULL DEFAULT '{}',
    state_version bigint NOT NULL CHECK (state_version >= 0),
    occurred_at timestamptz NOT NULL,
    received_at timestamptz NOT NULL DEFAULT now(),
    CHECK (jsonb_typeof(reported_state) = 'object')
);

CREATE INDEX ix_device_state_history_device_time
    ON device_state_history (device_id, occurred_at DESC);

CREATE TABLE environment_readings (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    message_id uuid NOT NULL UNIQUE,
    classroom_id uuid NOT NULL REFERENCES classrooms(id) ON DELETE RESTRICT,
    sensor_id text NOT NULL,
    temperature_c numeric(6, 3) NOT NULL CHECK (temperature_c BETWEEN -20 AND 80),
    relative_humidity_pct numeric(6, 3) NOT NULL CHECK (relative_humidity_pct BETWEEN 0 AND 100),
    illuminance_lux numeric(12, 3) CHECK (illuminance_lux BETWEEN 0 AND 200000),
    illuminance_adc integer CHECK (illuminance_adc IS NULL OR illuminance_adc BETWEEN 0 AND 4095),
    motion_detected boolean,
    sample_interval_ms integer CHECK (sample_interval_ms IS NULL OR sample_interval_ms > 0),
    quality jsonb NOT NULL DEFAULT '{}'::jsonb,
    occurred_at timestamptz NOT NULL,
    received_at timestamptz NOT NULL DEFAULT now(),
    CHECK (jsonb_typeof(quality) = 'object'),
    CONSTRAINT ck_environment_readings_light_value
        CHECK (illuminance_lux IS NOT NULL OR illuminance_adc IS NOT NULL)
);

CREATE INDEX ix_environment_readings_classroom_time
    ON environment_readings (classroom_id, occurred_at DESC);

CREATE TABLE environment_latest (
    classroom_id uuid PRIMARY KEY REFERENCES classrooms(id) ON DELETE CASCADE,
    reading_id uuid NOT NULL REFERENCES environment_readings(id) ON DELETE RESTRICT,
    temperature_c numeric(6, 3) NOT NULL,
    relative_humidity_pct numeric(6, 3) NOT NULL,
    illuminance_lux numeric(12, 3),
    illuminance_adc integer CHECK (illuminance_adc IS NULL OR illuminance_adc BETWEEN 0 AND 4095),
    motion_detected boolean,
    observed_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now()
);

-- Privacy: this table stores aggregate counts, not frames, crops, faces or
-- process-local ByteTrack identifiers.
CREATE TABLE occupancy_observations (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    message_id uuid NOT NULL UNIQUE,
    classroom_id uuid NOT NULL REFERENCES classrooms(id) ON DELETE RESTRICT,
    camera_id text NOT NULL,
    source text NOT NULL,
    person_count integer NOT NULL CHECK (person_count >= 0),
    confidence numeric(5, 4) NOT NULL CHECK (confidence BETWEEN 0 AND 1),
    entries_delta integer NOT NULL DEFAULT 0 CHECK (entries_delta >= 0),
    exits_delta integer NOT NULL DEFAULT 0 CHECK (exits_delta >= 0),
    frame_sequence bigint CHECK (frame_sequence IS NULL OR frame_sequence >= 0),
    occurred_at timestamptz NOT NULL,
    received_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX ix_occupancy_observations_classroom_time
    ON occupancy_observations (classroom_id, occurred_at DESC);

CREATE TABLE occupancy_current (
    classroom_id uuid PRIMARY KEY REFERENCES classrooms(id) ON DELETE CASCADE,
    observation_id uuid REFERENCES occupancy_observations(id) ON DELETE SET NULL,
    status occupancy_status NOT NULL DEFAULT 'unknown',
    person_count integer CHECK (person_count IS NULL OR person_count >= 0),
    confidence numeric(5, 4) CHECK (confidence IS NULL OR confidence BETWEEN 0 AND 1),
    vacancy_candidate_since timestamptz,
    observed_at timestamptz,
    updated_at timestamptz NOT NULL DEFAULT now(),
    CHECK (status <> 'occupied' OR person_count > 0),
    CHECK (status <> 'vacant_confirmed' OR person_count = 0)
);

CREATE TABLE occupancy_automation_state (
    classroom_id uuid PRIMARY KEY REFERENCES classrooms(id) ON DELETE CASCADE,
    qualifying_since timestamptz,
    below_threshold_since timestamptz,
    pir_scan_until timestamptz,
    fallback_lights_latched boolean NOT NULL DEFAULT false,
    last_motion_detected boolean,
    camera_was_fresh boolean NOT NULL DEFAULT false,
    urgency_started_at timestamptz,
    urgency_until timestamptz,
    urgency_result text NOT NULL DEFAULT 'inactive'
        CHECK (urgency_result IN (
            'inactive', 'monitoring', 'completed_no_presence',
            'completed_presence', 'completed_camera_unknown'
        )),
    urgency_restore_states jsonb NOT NULL DEFAULT '{}'::jsonb,
    updated_at timestamptz NOT NULL DEFAULT now(),
    CHECK (jsonb_typeof(urgency_restore_states) = 'object'),
    CHECK (
        (urgency_started_at IS NULL AND urgency_until IS NULL)
        OR (urgency_started_at IS NOT NULL AND urgency_until > urgency_started_at)
    )
);

CREATE TABLE model_versions (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    name text NOT NULL,
    version text NOT NULL,
    feature_set_version text NOT NULL,
    artifact_uri text NOT NULL,
    artifact_sha256 text NOT NULL,
    metrics jsonb NOT NULL DEFAULT '{}'::jsonb,
    trained_at timestamptz,
    activated_at timestamptz,
    retired_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK (jsonb_typeof(metrics) = 'object'),
    UNIQUE (name, version)
);

CREATE TABLE prediction_runs (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    classroom_id uuid NOT NULL REFERENCES classrooms(id) ON DELETE RESTRICT,
    model_version_id uuid NOT NULL REFERENCES model_versions(id) ON DELETE RESTRICT,
    horizon_seconds integer NOT NULL DEFAULT 60 CHECK (horizon_seconds = 60),
    leave_probability numeric(7, 6) NOT NULL CHECK (leave_probability BETWEEN 0 AND 1),
    feature_set_version text NOT NULL,
    feature_snapshot jsonb NOT NULL,
    predicted_at timestamptz NOT NULL,
    expires_at timestamptz NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK (expires_at > predicted_at),
    CHECK (jsonb_typeof(feature_snapshot) = 'object')
);

CREATE INDEX ix_prediction_runs_classroom_time
    ON prediction_runs (classroom_id, predicted_at DESC);

CREATE TABLE ai_recommendations (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    classroom_id uuid NOT NULL REFERENCES classrooms(id) ON DELETE RESTRICT,
    device_id uuid REFERENCES devices(id) ON DELETE RESTRICT,
    prediction_id uuid REFERENCES prediction_runs(id) ON DELETE SET NULL,
    recommended_action command_action NOT NULL,
    recommended_value jsonb NOT NULL,
    reason text NOT NULL,
    confidence numeric(7, 6) CHECK (confidence IS NULL OR confidence BETWEEN 0 AND 1),
    status recommendation_status NOT NULL DEFAULT 'pending',
    created_at timestamptz NOT NULL DEFAULT now(),
    expires_at timestamptz NOT NULL,
    resolved_at timestamptz,
    CHECK (expires_at > created_at),
    CHECK (jsonb_typeof(recommended_value) = 'object')
);

CREATE INDEX ix_ai_recommendations_classroom_time
    ON ai_recommendations (classroom_id, created_at DESC);

-- All triggers, including AI recommendations and schedules, first become a
-- control request. This table is not a device command/outbox.
CREATE TABLE control_requests (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    classroom_id uuid NOT NULL REFERENCES classrooms(id) ON DELETE RESTRICT,
    device_id uuid NOT NULL REFERENCES devices(id) ON DELETE RESTRICT,
    trigger control_trigger NOT NULL,
    requested_action command_action NOT NULL,
    requested_value jsonb NOT NULL,
    requested_by uuid REFERENCES app_users(id) ON DELETE SET NULL,
    recommendation_id uuid REFERENCES ai_recommendations(id) ON DELETE SET NULL,
    reason text NOT NULL,
    idempotency_key text,
    requested_at timestamptz NOT NULL DEFAULT now(),
    expires_at timestamptz NOT NULL,
    CHECK (expires_at > requested_at),
    CHECK (jsonb_typeof(requested_value) = 'object'),
    CHECK ((trigger = 'manual' AND requested_by IS NOT NULL) OR trigger <> 'manual'),
    CHECK ((trigger = 'ai_recommendation' AND recommendation_id IS NOT NULL)
           OR trigger <> 'ai_recommendation')
);

CREATE UNIQUE INDEX uq_control_requests_actor_idempotency
    ON control_requests (requested_by, idempotency_key)
    WHERE requested_by IS NOT NULL AND idempotency_key IS NOT NULL;
CREATE INDEX ix_control_requests_classroom_time
    ON control_requests (classroom_id, requested_at DESC);

-- Immutable policy evaluation. An allowed decision contains the exact bounded
-- command; a denied decision has no bounded output.
CREATE TABLE control_decisions (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    request_id uuid NOT NULL UNIQUE REFERENCES control_requests(id) ON DELETE RESTRICT,
    policy_version_id uuid NOT NULL REFERENCES control_policy_versions(id) ON DELETE RESTRICT,
    outcome decision_outcome NOT NULL,
    reason_codes text[] NOT NULL,
    summary text NOT NULL,
    input_snapshot jsonb NOT NULL,
    matched_rule text NOT NULL,
    bounded_action command_action,
    bounded_value jsonb,
    evaluated_at timestamptz NOT NULL DEFAULT now(),
    CHECK (array_length(reason_codes, 1) >= 1),
    CHECK (jsonb_typeof(input_snapshot) = 'object'),
    CHECK (
        (outcome = 'allowed' AND bounded_action IS NOT NULL
         AND jsonb_typeof(bounded_value) = 'object')
        OR
        (outcome = 'denied' AND bounded_action IS NULL AND bounded_value IS NULL)
    )
);

CREATE INDEX ix_control_decisions_time ON control_decisions (evaluated_at DESC);

-- This is both the durable command record and transactional outbox. Only rows
-- backed by an allowed decision can be inserted (enforced by trigger below).
CREATE TABLE device_commands (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    decision_id uuid NOT NULL REFERENCES control_decisions(id) ON DELETE RESTRICT,
    classroom_id uuid NOT NULL REFERENCES classrooms(id) ON DELETE RESTRICT,
    device_id uuid NOT NULL REFERENCES devices(id) ON DELETE RESTRICT,
    action command_action NOT NULL,
    value jsonb NOT NULL,
    idempotency_key text NOT NULL UNIQUE,
    status command_status NOT NULL DEFAULT 'queued',
    retry_count integer NOT NULL DEFAULT 0 CHECK (retry_count >= 0),
    next_attempt_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    published_at timestamptz,
    acknowledged_at timestamptz,
    applied_at timestamptz,
    expires_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now(),
    CHECK (expires_at > created_at),
    CHECK (jsonb_typeof(value) = 'object')
);

CREATE INDEX ix_device_commands_dispatch
    ON device_commands (status, next_attempt_at, created_at)
    WHERE status IN ('queued', 'published');
CREATE INDEX ix_device_commands_device_time
    ON device_commands (device_id, created_at DESC);

CREATE TABLE command_events (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    command_id uuid NOT NULL REFERENCES device_commands(id) ON DELETE RESTRICT,
    message_id uuid UNIQUE,
    status command_status NOT NULL,
    reason_code text,
    details jsonb NOT NULL DEFAULT '{}'::jsonb,
    reported_state jsonb,
    occurred_at timestamptz NOT NULL,
    received_at timestamptz NOT NULL DEFAULT now(),
    CHECK (jsonb_typeof(details) = 'object'),
    CHECK (reported_state IS NULL OR jsonb_typeof(reported_state) = 'object')
);

CREATE INDEX ix_command_events_command_time
    ON command_events (command_id, occurred_at);

CREATE TABLE manual_overrides (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    classroom_id uuid NOT NULL REFERENCES classrooms(id) ON DELETE RESTRICT,
    device_id uuid NOT NULL REFERENCES devices(id) ON DELETE RESTRICT,
    created_by uuid NOT NULL REFERENCES app_users(id) ON DELETE RESTRICT,
    mode override_mode NOT NULL,
    value jsonb,
    reason text NOT NULL,
    status override_status NOT NULL DEFAULT 'scheduled',
    starts_at timestamptz NOT NULL,
    ends_at timestamptz NOT NULL,
    cancelled_at timestamptz,
    cancelled_by uuid REFERENCES app_users(id) ON DELETE SET NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CHECK (ends_at > starts_at),
    CHECK ((mode = 'force_state' AND jsonb_typeof(value) = 'object')
           OR (mode = 'suspend_automation' AND value IS NULL))
);

CREATE INDEX ix_manual_overrides_active
    ON manual_overrides (device_id, starts_at, ends_at)
    WHERE status IN ('scheduled', 'active');

CREATE TABLE schedules (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    classroom_id uuid NOT NULL REFERENCES classrooms(id) ON DELETE RESTRICT,
    device_id uuid NOT NULL REFERENCES devices(id) ON DELETE RESTRICT,
    name text NOT NULL,
    action command_action NOT NULL,
    value jsonb NOT NULL,
    rrule text NOT NULL,
    timezone text NOT NULL,
    starts_at timestamptz NOT NULL,
    ends_at timestamptz,
    max_lateness_seconds integer NOT NULL DEFAULT 60 CHECK (max_lateness_seconds >= 0),
    enabled boolean NOT NULL DEFAULT true,
    version integer NOT NULL DEFAULT 1 CHECK (version > 0),
    next_run_at timestamptz,
    created_by uuid NOT NULL REFERENCES app_users(id) ON DELETE RESTRICT,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CHECK (ends_at IS NULL OR ends_at > starts_at),
    CHECK (jsonb_typeof(value) = 'object'),
    UNIQUE (classroom_id, name)
);

CREATE INDEX ix_schedules_due
    ON schedules (next_run_at)
    WHERE enabled = true;

CREATE TABLE schedule_runs (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    schedule_id uuid NOT NULL REFERENCES schedules(id) ON DELETE RESTRICT,
    scheduled_for timestamptz NOT NULL,
    started_at timestamptz,
    finished_at timestamptz,
    status schedule_run_status NOT NULL DEFAULT 'due',
    control_request_id uuid REFERENCES control_requests(id) ON DELETE SET NULL,
    reason_code text,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (schedule_id, scheduled_for)
);

CREATE TABLE energy_readings (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    message_id uuid NOT NULL UNIQUE,
    classroom_id uuid NOT NULL REFERENCES classrooms(id) ON DELETE RESTRICT,
    device_id uuid NOT NULL REFERENCES devices(id) ON DELETE RESTRICT,
    voltage_v numeric(10, 3) CHECK (voltage_v IS NULL OR voltage_v BETWEEN 0 AND 300),
    current_a numeric(12, 4) CHECK (current_a IS NULL OR current_a >= 0),
    active_power_w numeric(14, 4) NOT NULL CHECK (active_power_w >= 0),
    apparent_power_va numeric(14, 4) CHECK (apparent_power_va IS NULL OR apparent_power_va >= 0),
    power_factor numeric(6, 5) CHECK (power_factor IS NULL OR power_factor BETWEEN 0 AND 1),
    energy_wh_total numeric(18, 4) CHECK (energy_wh_total IS NULL OR energy_wh_total >= 0),
    sample_interval_ms integer CHECK (sample_interval_ms IS NULL OR sample_interval_ms > 0),
    meter_reset boolean NOT NULL DEFAULT false,
    occurred_at timestamptz NOT NULL,
    received_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX ix_energy_readings_classroom_time
    ON energy_readings (classroom_id, occurred_at DESC);
CREATE INDEX ix_energy_readings_device_time
    ON energy_readings (device_id, occurred_at DESC);

CREATE TABLE energy_baseline_versions (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    name text NOT NULL,
    version text NOT NULL,
    method text NOT NULL,
    parameters jsonb NOT NULL,
    trained_from timestamptz,
    trained_to timestamptz,
    metrics jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK (jsonb_typeof(parameters) = 'object'),
    CHECK (jsonb_typeof(metrics) = 'object'),
    UNIQUE (name, version)
);

CREATE TABLE energy_savings_estimates (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    classroom_id uuid NOT NULL REFERENCES classrooms(id) ON DELETE RESTRICT,
    device_id uuid REFERENCES devices(id) ON DELETE RESTRICT,
    baseline_version_id uuid NOT NULL REFERENCES energy_baseline_versions(id) ON DELETE RESTRICT,
    period_start timestamptz NOT NULL,
    period_end timestamptz NOT NULL,
    baseline_energy_wh numeric(18, 4) NOT NULL CHECK (baseline_energy_wh >= 0),
    actual_energy_wh numeric(18, 4) NOT NULL CHECK (actual_energy_wh >= 0),
    estimated_savings_wh numeric(18, 4) NOT NULL,
    estimated_cost_savings numeric(18, 6),
    currency char(3),
    quality_score numeric(7, 6) CHECK (quality_score IS NULL OR quality_score BETWEEN 0 AND 1),
    methodology_notes text,
    calculated_at timestamptz NOT NULL DEFAULT now(),
    CHECK (period_end > period_start),
    UNIQUE (classroom_id, device_id, baseline_version_id, period_start, period_end)
);

CREATE INDEX ix_energy_savings_classroom_period
    ON energy_savings_estimates (classroom_id, period_start DESC);

-- Deduplication and quarantine metadata. Payload is bounded by application
-- limits and must never contain camera frames.
CREATE TABLE ingested_messages (
    message_id uuid PRIMARY KEY,
    topic text NOT NULL,
    producer_id text,
    payload_sha256 text NOT NULL,
    payload jsonb,
    validation_status text NOT NULL CHECK (validation_status IN ('accepted', 'rejected')),
    rejection_reason text,
    occurred_at timestamptz,
    received_at timestamptz NOT NULL DEFAULT now(),
    CHECK (payload IS NULL OR jsonb_typeof(payload) = 'object')
);

CREATE INDEX ix_ingested_messages_received_at
    ON ingested_messages (received_at DESC);

CREATE TABLE system_events (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    classroom_id uuid REFERENCES classrooms(id) ON DELETE RESTRICT,
    device_id uuid REFERENCES devices(id) ON DELETE RESTRICT,
    severity event_severity NOT NULL,
    status event_status NOT NULL DEFAULT 'open',
    code text NOT NULL,
    message text NOT NULL,
    details jsonb NOT NULL DEFAULT '{}'::jsonb,
    occurred_at timestamptz NOT NULL,
    acknowledged_at timestamptz,
    acknowledged_by uuid REFERENCES app_users(id) ON DELETE SET NULL,
    resolved_at timestamptz,
    CHECK (jsonb_typeof(details) = 'object')
);

CREATE INDEX ix_system_events_open
    ON system_events (classroom_id, severity, occurred_at DESC)
    WHERE status = 'open';

CREATE TABLE audit_log (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    actor_id uuid REFERENCES app_users(id) ON DELETE SET NULL,
    action text NOT NULL,
    resource_type text NOT NULL,
    resource_id uuid,
    classroom_id uuid REFERENCES classrooms(id) ON DELETE RESTRICT,
    trace_id uuid,
    before_data jsonb,
    after_data jsonb,
    occurred_at timestamptz NOT NULL DEFAULT now(),
    CHECK (before_data IS NULL OR jsonb_typeof(before_data) = 'object'),
    CHECK (after_data IS NULL OR jsonb_typeof(after_data) = 'object')
);

CREATE INDEX ix_audit_log_resource
    ON audit_log (resource_type, resource_id, occurred_at DESC);

CREATE OR REPLACE FUNCTION set_updated_at()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_app_users_updated_at
    BEFORE UPDATE ON app_users
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();
CREATE TRIGGER trg_sites_updated_at
    BEFORE UPDATE ON sites
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();
CREATE TRIGGER trg_classrooms_updated_at
    BEFORE UPDATE ON classrooms
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();
CREATE TRIGGER trg_devices_updated_at
    BEFORE UPDATE ON devices
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();
CREATE TRIGGER trg_device_states_updated_at
    BEFORE UPDATE ON device_states
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();
CREATE TRIGGER trg_manual_overrides_updated_at
    BEFORE UPDATE ON manual_overrides
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();
CREATE TRIGGER trg_schedules_updated_at
    BEFORE UPDATE ON schedules
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();
CREATE TRIGGER trg_device_commands_updated_at
    BEFORE UPDATE ON device_commands
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- Defense in depth: a command can only copy the bounded output of an allowed
-- policy decision for the same classroom/device.
CREATE OR REPLACE FUNCTION enforce_allowed_command()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    v_outcome decision_outcome;
    v_action command_action;
    v_value jsonb;
    v_classroom_id uuid;
    v_device_id uuid;
BEGIN
    SELECT d.outcome, d.bounded_action, d.bounded_value,
           r.classroom_id, r.device_id
      INTO v_outcome, v_action, v_value, v_classroom_id, v_device_id
      FROM control_decisions d
      JOIN control_requests r ON r.id = d.request_id
     WHERE d.id = NEW.decision_id;

    IF NOT FOUND OR v_outcome <> 'allowed' THEN
        RAISE EXCEPTION 'device command requires an allowed control decision';
    END IF;
    IF NEW.classroom_id <> v_classroom_id OR NEW.device_id <> v_device_id THEN
        RAISE EXCEPTION 'command target does not match its control request';
    END IF;
    IF NEW.action <> v_action OR NEW.value <> v_value THEN
        RAISE EXCEPTION 'command must equal the bounded policy output';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_device_commands_allowed_decision
    BEFORE INSERT OR UPDATE OF decision_id, classroom_id, device_id, action, value
    ON device_commands
    FOR EACH ROW EXECUTE FUNCTION enforce_allowed_command();

-- Policy decisions and command events form an append-only audit chain.
CREATE OR REPLACE FUNCTION prevent_audit_record_mutation()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    RAISE EXCEPTION '% rows are immutable', TG_TABLE_NAME;
END;
$$;

CREATE TRIGGER trg_control_decisions_immutable
    BEFORE UPDATE OR DELETE ON control_decisions
    FOR EACH ROW EXECUTE FUNCTION prevent_audit_record_mutation();
CREATE TRIGGER trg_command_events_immutable
    BEFORE UPDATE OR DELETE ON command_events
    FOR EACH ROW EXECUTE FUNCTION prevent_audit_record_mutation();

-- Analytics evidence is independent of desired/operational control state.
CREATE TABLE analytics_runtime_segments (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    classroom_id uuid NOT NULL REFERENCES classrooms(id) ON DELETE RESTRICT,
    device_id uuid NOT NULL REFERENCES devices(id) ON DELETE RESTRICT,
    state text NOT NULL CHECK (state IN ('on', 'off', 'unknown')),
    mode text NOT NULL CHECK (mode IN ('auto', 'manual', 'unknown')),
    first_observed_at timestamptz NOT NULL,
    last_observed_at timestamptz NOT NULL,
    freshness_limit_seconds integer NOT NULL DEFAULT 15
        CHECK (freshness_limit_seconds BETWEEN 2 AND 300),
    source text NOT NULL DEFAULT 'esp32_live_state'
        CHECK (source = 'esp32_live_state'),
    timestamp_kind text NOT NULL DEFAULT 'observation'
        CHECK (timestamp_kind = 'observation'),
    first_message_id uuid NOT NULL,
    last_message_id uuid NOT NULL,
    quality text[] NOT NULL DEFAULT '{}',
    CHECK (last_observed_at >= first_observed_at),
    UNIQUE (device_id, first_observed_at)
);

CREATE INDEX ix_analytics_runtime_device_time
    ON analytics_runtime_segments (device_id, last_observed_at DESC);
CREATE INDEX ix_analytics_runtime_classroom_time
    ON analytics_runtime_segments (classroom_id, first_observed_at, last_observed_at);

COMMIT;
