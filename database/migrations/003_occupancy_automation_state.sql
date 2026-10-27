BEGIN;

CREATE TABLE IF NOT EXISTS occupancy_automation_state (
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

COMMIT;
