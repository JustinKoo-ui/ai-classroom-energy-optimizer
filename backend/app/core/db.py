"""Database engine and table definitions used by ingestion."""

from __future__ import annotations

from sqlalchemy import JSON, DateTime, Integer, MetaData, Numeric, Table, Text, Column
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

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
    Column("illuminance_lux", Numeric(12, 3), nullable=False),
    Column("sample_interval_ms", Integer),
    Column("quality", JSON, nullable=False),
    Column("occurred_at", DateTime(timezone=True), nullable=False),
)


def create_database(database_url: str) -> tuple[AsyncEngine, async_sessionmaker]:
    """Create an async engine and session factory without opening a connection."""
    engine = create_async_engine(database_url, pool_pre_ping=True)
    return engine, async_sessionmaker(engine, expire_on_commit=False)
