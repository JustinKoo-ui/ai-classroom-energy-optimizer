BEGIN;

ALTER TABLE environment_readings
    ADD COLUMN IF NOT EXISTS device_occurred_at timestamptz,
    ADD COLUMN IF NOT EXISTS classroom_telemetry jsonb,
    ALTER COLUMN temperature_c DROP NOT NULL,
    ALTER COLUMN relative_humidity_pct DROP NOT NULL,
    DROP CONSTRAINT IF EXISTS environment_readings_temperature_c_check,
    DROP CONSTRAINT IF EXISTS ck_environment_readings_light_value,
    DROP CONSTRAINT IF EXISTS ck_environment_readings_classroom_telemetry;

ALTER TABLE environment_readings
    ADD CONSTRAINT environment_readings_temperature_c_check CHECK (
        temperature_c BETWEEN CASE WHEN classroom_telemetry IS NULL THEN -20 ELSE -40 END AND 80
    ),
    ADD CONSTRAINT ck_environment_readings_classroom_telemetry CHECK (
        classroom_telemetry IS NOT NULL AND jsonb_typeof(classroom_telemetry) = 'object'
        OR classroom_telemetry IS NULL AND device_occurred_at IS NULL
           AND temperature_c IS NOT NULL AND relative_humidity_pct IS NOT NULL
    ),
    ADD CONSTRAINT ck_environment_readings_light_value CHECK (
        classroom_telemetry IS NOT NULL OR illuminance_lux IS NOT NULL
        OR illuminance_adc IS NOT NULL
    );

CREATE INDEX IF NOT EXISTS ix_environment_readings_classroom_telemetry_time
    ON environment_readings (
        classroom_id, sensor_id, occurred_at DESC, received_at DESC, message_id DESC
    ) WHERE classroom_telemetry IS NOT NULL;

COMMIT;
