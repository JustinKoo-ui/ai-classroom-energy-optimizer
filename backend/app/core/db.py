"""Database engine and table definitions used by ingestion."""

from __future__ import annotations

from sqlalchemy import JSON, Boolean, Column, DateTime, Integer, MetaData, Numeric, Table, Text
from sqlalchemy.dialects.postgresql import ENUM, UUID
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

metadata = MetaData()

environment_readings = Table(
    "environment_readings",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("message_id", UUID(as_uuid=True), nullable=False),
    Column("classroom_id", UUID(as_uuid=True), nullable=False),
    Column("sensor_id", Text, nullable=False),
    Column("temperature_c", Numeric(6, 3), nullable=False),
    Column("relative_humidity_pct", Numeric(6, 3), nullable=False),
    Column("illuminance_lux", Numeric(12, 3)),
    Column("illuminance_adc", Integer),
    Column("motion_detected", Boolean),
    Column("sample_interval_ms", Integer),
    Column("quality", JSON, nullable=False),
    Column("occurred_at", DateTime(timezone=True), nullable=False),
)

occupancy_observations = Table(
    "occupancy_observations",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("message_id", UUID(as_uuid=True), nullable=False),
    Column("classroom_id", UUID(as_uuid=True), nullable=False),
    Column("camera_id", Text, nullable=False),
    Column("source", Text, nullable=False),
    Column("person_count", Integer, nullable=False),
    Column("confidence", Numeric(5, 4), nullable=False),
    Column("entries_delta", Integer, nullable=False, default=0),
    Column("exits_delta", Integer, nullable=False, default=0),
    Column("frame_sequence", Integer),
    Column("occurred_at", DateTime(timezone=True), nullable=False),
)

occupancy_current = Table(
    "occupancy_current",
    metadata,
    Column("classroom_id", UUID(as_uuid=True), primary_key=True),
    Column("observation_id", UUID(as_uuid=True)),
    Column(
        "status",
        ENUM(
            "occupied",
            "possibly_vacant",
            "vacant_confirmed",
            "unknown",
            name="occupancy_status",
            create_type=False,
        ),
        nullable=False,
    ),
    Column("person_count", Integer),
    Column("confidence", Numeric(5, 4)),
    Column("vacancy_candidate_since", DateTime(timezone=True)),
    Column("observed_at", DateTime(timezone=True)),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)

def create_database(
    database_url: str,
) -> tuple[AsyncEngine, async_sessionmaker[AsyncSession]]:
    """Create an async engine and session factory without opening a connection."""
    engine = create_async_engine(database_url, pool_pre_ping=True)
    return engine, async_sessionmaker[AsyncSession](engine, expire_on_commit=False)
