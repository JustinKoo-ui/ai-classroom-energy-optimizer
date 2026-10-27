BEGIN;

-- Live ESP32 software-state evidence. No command or control state is mutated.
-- Repeated observations extend a segment only while the freshness gap is <= TTL.
CREATE TABLE IF NOT EXISTS analytics_runtime_segments (
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

CREATE INDEX IF NOT EXISTS ix_analytics_runtime_device_time
    ON analytics_runtime_segments (device_id, last_observed_at DESC);
CREATE INDEX IF NOT EXISTS ix_analytics_runtime_classroom_time
    ON analytics_runtime_segments (classroom_id, first_observed_at, last_observed_at);

COMMIT;
