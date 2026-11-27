BEGIN;

CREATE TABLE IF NOT EXISTS solar_tracker_messages (
    message_id uuid PRIMARY KEY,
    site_id uuid NOT NULL REFERENCES sites(id) ON DELETE RESTRICT,
    classroom_id uuid NOT NULL REFERENCES classrooms(id) ON DELETE RESTRICT,
    producer_id text NOT NULL CHECK (producer_id ~ '^[A-Za-z0-9_-]{1,80}$'),
    message_kind text NOT NULL CHECK (message_kind IN ('telemetry', 'status')),
    occurred_at timestamptz,
    received_at timestamptz NOT NULL,
    payload jsonb NOT NULL CHECK (jsonb_typeof(payload) = 'object')
);

CREATE INDEX IF NOT EXISTS ix_solar_tracker_messages_source_time
    ON solar_tracker_messages (
        site_id, classroom_id, producer_id, message_kind, received_at DESC, message_id DESC
    );

CREATE INDEX IF NOT EXISTS ix_environment_readings_sensor_receipt
    ON environment_readings (classroom_id, sensor_id, received_at DESC);

CREATE TABLE IF NOT EXISTS relay_monitor_receipts (
    classroom_id uuid PRIMARY KEY REFERENCES classrooms(id) ON DELETE CASCADE,
    occurred_at timestamptz NOT NULL,
    received_at timestamptz NOT NULL
);

COMMIT;
