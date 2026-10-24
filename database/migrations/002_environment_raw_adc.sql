BEGIN;

ALTER TABLE environment_readings
    ALTER COLUMN illuminance_lux DROP NOT NULL,
    ADD COLUMN IF NOT EXISTS illuminance_adc integer
        CHECK (illuminance_adc IS NULL OR illuminance_adc BETWEEN 0 AND 4095),
    ADD COLUMN IF NOT EXISTS motion_detected boolean;

ALTER TABLE environment_latest
    ALTER COLUMN illuminance_lux DROP NOT NULL,
    ADD COLUMN IF NOT EXISTS illuminance_adc integer
        CHECK (illuminance_adc IS NULL OR illuminance_adc BETWEEN 0 AND 4095),
    ADD COLUMN IF NOT EXISTS motion_detected boolean;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'ck_environment_readings_light_value'
    ) THEN
        ALTER TABLE environment_readings
            ADD CONSTRAINT ck_environment_readings_light_value
            CHECK (illuminance_lux IS NOT NULL OR illuminance_adc IS NOT NULL);
    END IF;
END
$$;

COMMIT;
