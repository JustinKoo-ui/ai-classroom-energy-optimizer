"""Persistence adapter for validated occupancy observations."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import and_, case, func, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import occupancy_current, occupancy_observations
from app.schemas.mqtt import MqttEnvelope, OccupancyObservationPayload


@dataclass(frozen=True)
class CurrentOccupancy:
    classroom_exists: bool
    person_count: int | None
    confidence: float | None
    observed_at: datetime | None
    status: str | None


async def insert_occupancy_observation(
    session: AsyncSession,
    envelope: MqttEnvelope[OccupancyObservationPayload],
) -> bool:
    """Persist one observation and return true when a new message_id was stored."""
    payload = envelope.payload
    observation_id = uuid4()
    statement = (
        insert(occupancy_observations)
        .values(
            id=observation_id,
            message_id=envelope.message_id,
            classroom_id=envelope.classroom_id,
            camera_id=payload.camera_id,
            source=payload.source,
            person_count=payload.count,
            confidence=payload.confidence,
            entries_delta=payload.entries_delta,
            exits_delta=payload.exits_delta,
            frame_sequence=payload.frame_sequence,
            occurred_at=envelope.occurred_at,
        )
        .on_conflict_do_nothing(index_elements=[occupancy_observations.c.message_id])
        .returning(occupancy_observations.c.message_id)
    )
    result = await session.execute(statement)
    saved = result.scalar_one_or_none() is not None
    if saved:
        status = "occupied" if payload.count > 0 else "possibly_vacant"
        current_statement = insert(occupancy_current).values(
            classroom_id=envelope.classroom_id,
            observation_id=observation_id,
            status=status,
            person_count=payload.count,
            confidence=payload.confidence,
            vacancy_candidate_since=envelope.occurred_at if payload.count == 0 else None,
            observed_at=envelope.occurred_at,
            updated_at=datetime.now(UTC),
        )
        excluded = current_statement.excluded
        vacancy_candidate_since = (
            case(
                (
                    and_(
                        occupancy_current.c.status == "possibly_vacant",
                        occupancy_current.c.observed_at
                        >= excluded.observed_at - timedelta(seconds=15),
                    ),
                    func.coalesce(
                        occupancy_current.c.vacancy_candidate_since,
                        excluded.observed_at,
                    ),
                ),
                else_=excluded.observed_at,
            )
            if payload.count == 0
            else None
        )
        await session.execute(
            current_statement.on_conflict_do_update(
                index_elements=[occupancy_current.c.classroom_id],
                set_={
                    "observation_id": excluded.observation_id,
                    "status": excluded.status,
                    "person_count": excluded.person_count,
                    "confidence": excluded.confidence,
                    "vacancy_candidate_since": vacancy_candidate_since,
                    "observed_at": excluded.observed_at,
                    "updated_at": excluded.updated_at,
                },
                where=occupancy_current.c.observed_at < excluded.observed_at,
            )
        )
    await session.commit()
    return saved


async def get_current_occupancy(
    session: AsyncSession,
    classroom_id: UUID,
) -> CurrentOccupancy:
    result = await session.execute(
        text(
            """
            SELECT
                EXISTS (SELECT 1 FROM classrooms WHERE id = :classroom_id) AS classroom_exists,
                oc.person_count,
                oc.confidence,
                oc.observed_at,
                oc.status
            FROM (SELECT 1) AS singleton
            LEFT JOIN occupancy_current AS oc
                ON oc.classroom_id = :classroom_id
            """
        ),
        {"classroom_id": classroom_id},
    )
    row = result.mappings().one()
    return CurrentOccupancy(
        classroom_exists=row["classroom_exists"],
        person_count=row["person_count"],
        confidence=float(row["confidence"]) if row["confidence"] is not None else None,
        observed_at=row["observed_at"],
        status=row["status"],
    )
