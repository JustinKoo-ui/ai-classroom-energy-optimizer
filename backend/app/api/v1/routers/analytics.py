"""Local prototype analytics. No route can mutate devices or automation."""

import secrets
from datetime import date
from typing import Annotated, Any
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
from app.schemas.energy_chat import ChatRequest, ChatResponse
from app.services.analytics import get_analytics_report, get_historical_comparison
from app.services.energy_chat import EnergyChat
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


async def _report_and_context(
    request: Request,
    classroom_id: UUID,
    settings: Settings,
    selected_date: date | None,
    period: Period,
) -> tuple[AnalyticsReport, dict[str, Any]]:
    """Server-calculated metrics plus aggregate context shared by report and chat."""
    async with _get_sessions(request)() as session:
        report = await get_analytics_report(
            session,
            classroom_id,
            settings,
            selected_date,
            period,
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
    return report, context


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
        report, context = await _report_and_context(
            request, classroom_id, settings, body.date, body.period
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


@router.post("/{classroom_id}/analytics/chat", response_model=ChatResponse)
async def chat_about_analytics(
    classroom_id: UUID,
    body: ChatRequest,
    request: Request,
) -> ChatResponse:
    """Read-only questions about recorded data. Cannot reach devices or automation."""
    _require_loopback(request)
    settings = getattr(request.app.state, "analytics_settings", None) or Settings()
    _authenticate_analyst(request, settings)
    chat: EnergyChat | None = getattr(request.app.state, "energy_chat", None)
    if chat is None:
        return ChatResponse(status="unavailable", message="The AI assistant is unavailable.")
    try:
        report, context = await _report_and_context(
            request, classroom_id, settings, body.date, body.period
        )
        return await chat.answer(report, context, body.message, body.history)
    except SQLAlchemyError:
        return ChatResponse(
            status="unavailable",
            message="Room data is unavailable right now, so the AI assistant cannot answer.",
        )
    except (ValueError, OverflowError, ZoneInfoNotFoundError) as exc:
        raise HTTPException(
            422, "Invalid reporting configuration or inconsistent runtime data."
        ) from exc
