"""Local prototype analytics. No route can mutate devices or automation."""

import secrets
from datetime import date
from typing import Annotated
from uuid import UUID
from zoneinfo import ZoneInfoNotFoundError

from fastapi import APIRouter, HTTPException, Query, Request
from sqlalchemy.exc import SQLAlchemyError

from app.api.v1.routers.environment_analysis import _get_sessions, _require_loopback
from app.core.config import Settings
from app.domain.runtime import Period
from app.repositories.analyst_context import load_analyst_context
from app.schemas.analyst import AnalystRequest, AnalystResponse
from app.schemas.analytics import AnalyticsReport
from app.services.analytics import get_analytics_report, get_historical_comparison
from app.services.environmental_analyst import EnvironmentalAnalyst

router = APIRouter(prefix="/api/v1/classrooms", tags=["read-only analytics"])


@router.get("/{classroom_id}/analytics", response_model=AnalyticsReport)
async def read_analytics(
    classroom_id: UUID,
    request: Request,
    selected_date: Annotated[date | None, Query(alias="date")] = None,
    period: Period = "day",
) -> AnalyticsReport:
    _require_loopback(request)
    settings = getattr(request.app.state, "analytics_settings", None) or Settings()
    try:
        async with _get_sessions(request)() as session:
            report = await get_analytics_report(
                session,
                classroom_id,
                settings,
                selected_date,
                period,
            )
    except SQLAlchemyError as exc:
        raise HTTPException(503, "Analytics storage is unavailable; apply migration 004.") from exc
    except (ValueError, OverflowError, ZoneInfoNotFoundError) as exc:
        raise HTTPException(
            422, "Invalid reporting configuration or inconsistent runtime data."
        ) from exc
    if report is None:
        raise HTTPException(404, "Classroom not found.")
    return report


def _authenticate_analyst(request: Request, settings: Settings) -> None:
    expected = settings.analyst_access_token
    if expected is None or not expected.get_secret_value().strip():
        raise HTTPException(
            503, "AI commentary is not configured. Set a server report access token."
        )
    supplied = request.headers.get("authorization", "")
    wanted = f"Bearer {expected.get_secret_value()}"
    if not secrets.compare_digest(supplied.encode(), wanted.encode()):
        raise HTTPException(
            401, "A valid report access token is required.", headers={"WWW-Authenticate": "Bearer"}
        )


@router.post("/{classroom_id}/analytics/report", response_model=AnalystResponse)
async def generate_analyst_report(
    classroom_id: UUID,
    body: AnalystRequest,
    request: Request,
) -> AnalystResponse:
    _require_loopback(request)
    settings = getattr(request.app.state, "analytics_settings", None) or Settings()
    _authenticate_analyst(request, settings)
    analyst: EnvironmentalAnalyst | None = getattr(request.app.state, "environmental_analyst", None)
    if analyst is None:
        return AnalystResponse(
            status="unavailable", message="AI commentary is temporarily unavailable."
        )
    try:
        async with _get_sessions(request)() as session:
            report = await get_analytics_report(
                session,
                classroom_id,
                settings,
                body.date,
                body.period,
            )
            if report is None:
                raise HTTPException(404, "Classroom not found.")
            context = await load_analyst_context(
                session,
                classroom_id,
                report.period_start,
                report.evaluated_until,
            )
            context["historical_comparison"] = await get_historical_comparison(
                session,
                report,
                settings,
            )
        return await analyst.generate(report, context, refresh=body.refresh)
    except SQLAlchemyError:
        return AnalystResponse(
            status="unavailable",
            message="AI commentary data is unavailable. "
            "Runtime and energy analytics can be refreshed independently.",
        )
    except (ValueError, OverflowError, ZoneInfoNotFoundError) as exc:
        raise HTTPException(
            422, "Invalid reporting configuration or inconsistent runtime data."
        ) from exc
