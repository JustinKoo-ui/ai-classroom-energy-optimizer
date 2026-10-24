"""Create the local classroom fixture used by the localhost telemetry demo."""

from __future__ import annotations

import asyncio
import hashlib
import json
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.core.config import Settings
from app.core.db import create_database
from app.domain.local_hardware import (
    LOCAL_CLASSROOM_ID,
    LOCAL_OPERATOR_ID,
    LOCAL_RELAY_DEVICES,
    LOCAL_SITE_ID,
)

POLICY_ID = UUID("a5ca49c2-62da-44d8-a780-a9247ec7ab85")


async def main() -> None:
    settings = Settings()
    engine, _ = create_database(settings.database_url)
    policy_config = json.dumps(
        {
            "occupied_target_temperature_c": 26.0,
            "target_illuminance_lux": 300,
        },
        sort_keys=True,
    )
    try:
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    """
                    INSERT INTO control_policy_versions
                        (id, version, description, config, config_sha256)
                    VALUES
                        (:id, 'local-demo-v1', 'Local demo policy', CAST(:config AS jsonb), :hash)
                    ON CONFLICT (version) DO NOTHING
                    """
                ),
                {
                    "id": POLICY_ID,
                    "config": policy_config,
                    "hash": hashlib.sha256(policy_config.encode()).hexdigest(),
                },
            )
            policy_result = await connection.execute(
                text("SELECT id FROM control_policy_versions WHERE version = 'local-demo-v1'")
            )
            policy_id = UUID(str(policy_result.scalar_one()))

            site_id = await _get_or_create_site(connection)
            classroom_id = await _get_or_create_classroom(connection, site_id, policy_id)
            await _seed_local_operator(connection, site_id)
            await _seed_local_relay_devices(connection, classroom_id)
    finally:
        await engine.dispose()

    print(f"Local demo classroom ready: {classroom_id}")


async def _seed_local_operator(connection: AsyncConnection, site_id: UUID) -> None:
    await connection.execute(
        text(
            """
            INSERT INTO app_users (id, external_subject, email, display_name)
            VALUES (
                :id, 'local-demo-operator', 'local-operator@example.invalid',
                'Local Demo Operator'
            )
            ON CONFLICT (id) DO NOTHING
            """
        ),
        {"id": LOCAL_OPERATOR_ID},
    )
    await connection.execute(
        text(
            """
            INSERT INTO site_memberships (site_id, user_id, role)
            VALUES (:site_id, :user_id, 'operator')
            ON CONFLICT (site_id, user_id) DO UPDATE SET role = 'operator'
            """
        ),
        {"site_id": site_id, "user_id": LOCAL_OPERATOR_ID},
    )


async def _seed_local_relay_devices(
    connection: AsyncConnection,
    classroom_id: UUID,
) -> None:
    capabilities = json.dumps(
        {
            "supported_actions": ["set_power", "set_mode"],
            "supports_power": True,
            "supported_modes": ["auto", "manual"],
            "setpoint_min_c": None,
            "setpoint_max_c": None,
            "fan_speed_steps_pct": [],
            "brightness_steps_pct": [],
        }
    )
    for device in LOCAL_RELAY_DEVICES.values():
        await connection.execute(
            text(
                """
                INSERT INTO devices (
                    id, classroom_id, hardware_uid, mqtt_client_id, name, type,
                    manufacturer, model, capabilities, enabled
                )
                VALUES (
                    :id, :classroom_id, :hardware_uid, :mqtt_client_id, :name,
                    :type, 'local prototype', 'ESP32 GPIO relay', CAST(:capabilities AS jsonb),
                    true
                )
                ON CONFLICT (id) DO UPDATE
                SET classroom_id = EXCLUDED.classroom_id,
                    capabilities = EXCLUDED.capabilities,
                    enabled = true
                """
            ),
            {
                **device,
                "classroom_id": classroom_id,
                "capabilities": capabilities,
            },
        )
        await connection.execute(
            text(
                """
                INSERT INTO device_states (
                    device_id, operational_status, desired_state, reported_state
                )
                VALUES (
                    :device_id, 'registered',
                    '{"on": false, "mode": "auto"}'::jsonb,
                    '{"on": false, "mode": "auto"}'::jsonb
                )
                ON CONFLICT (device_id) DO NOTHING
                """
            ),
            {"device_id": device["id"]},
        )


async def _get_or_create_site(connection: AsyncConnection) -> UUID:
    site_id = await connection.scalar(text("SELECT id FROM sites WHERE code = 'LOCAL-DEMO'"))
    if site_id is not None:
        if UUID(str(site_id)) != LOCAL_SITE_ID:
            raise RuntimeError("LOCAL-DEMO site exists with an ID that does not match the MQTT ACL")
        return LOCAL_SITE_ID

    fixed_id_is_used = await connection.scalar(
        text("SELECT EXISTS (SELECT 1 FROM sites WHERE id = :id)"),
        {"id": LOCAL_SITE_ID},
    )
    if fixed_id_is_used:
        raise RuntimeError("The fixed local demo site ID is already used by another site")

    result = await connection.execute(
        text(
            """
            INSERT INTO sites (id, code, name, timezone)
            VALUES (:id, 'LOCAL-DEMO', 'Local demo site', 'Asia/Kuala_Lumpur')
            RETURNING id
            """
        ),
        {"id": LOCAL_SITE_ID},
    )
    return UUID(str(result.scalar_one()))


async def _get_or_create_classroom(
    connection: AsyncConnection,
    site_id: UUID,
    policy_id: UUID,
) -> UUID:
    classroom_id = await connection.scalar(
        text("SELECT id FROM classrooms WHERE site_id = :site_id AND code = 'LOCAL-ROOM'"),
        {"site_id": site_id},
    )
    if classroom_id is not None:
        if UUID(str(classroom_id)) != LOCAL_CLASSROOM_ID:
            raise RuntimeError(
                "LOCAL-ROOM classroom exists with an ID that does not match the MQTT ACL"
            )
        return LOCAL_CLASSROOM_ID

    fixed_id_is_used = await connection.scalar(
        text("SELECT EXISTS (SELECT 1 FROM classrooms WHERE id = :id)"),
        {"id": LOCAL_CLASSROOM_ID},
    )
    if fixed_id_is_used:
        raise RuntimeError("The fixed local classroom ID is already used by another classroom")

    result = await connection.execute(
        text(
            """
            INSERT INTO classrooms
                (id, site_id, code, name, capacity, timezone, control_policy_id)
            VALUES
                (:id, :site_id, 'LOCAL-ROOM', 'Local demo classroom', 30,
                 'Asia/Kuala_Lumpur', :policy_id)
            RETURNING id
            """
        ),
        {
            "id": LOCAL_CLASSROOM_ID,
            "site_id": site_id,
            "policy_id": policy_id,
        },
    )
    return UUID(str(result.scalar_one()))


if __name__ == "__main__":
    asyncio.run(main())
