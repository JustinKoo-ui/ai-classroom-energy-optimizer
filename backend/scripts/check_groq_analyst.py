"""Opt-in live Groq smoke test using synthetic storage and the real report route.

Run from backend: uv run python scripts/check_groq_analyst.py --live
No operational application lifespan, database, MQTT or device commands are started.
The provider key and local report token are loaded from backend/.env, never printed.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from datetime import date, timedelta
from typing import Any
from unittest.mock import AsyncMock, patch

import httpx
from fastapi import FastAPI
from pydantic import ValidationError

from app.api.v1.routers import analytics
from app.core.config import Settings
from app.domain.local_hardware import LOCAL_CLASSROOM_ID, LOCAL_RELAY_DEVICES
from app.domain.runtime import period_bounds
from app.repositories.runtime_history import RuntimeHistory
from app.schemas.analyst import AnalystSections
from app.services.energy_analytics import build_analytics_report
from app.services.environmental_analyst import EnvironmentalAnalyst


class DiagnosticTransport(httpx.AsyncBaseTransport):
    """Expose bounded diagnostic metadata, never headers or generated prose."""

    def __init__(self, settings: Settings) -> None:
        self.inner = httpx.AsyncHTTPTransport(retries=0)
        self.results: list[dict[str, Any]] = []
        self.secrets = [
            secret.get_secret_value()
            for secret in (settings.analyst_api_key, settings.analyst_access_token)
            if secret is not None
        ]

    def _redact(self, value: Any) -> str:
        result = str(value)
        for secret in self.secrets:
            if secret:
                result = result.replace(secret, "[redacted]")
        return result[:700]

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        response = await self.inner.handle_async_request(request)
        body = bytearray()
        async for chunk in response.aiter_bytes():
            if len(body) + len(chunk) > 100_000:
                await response.aclose()
                raise ValueError("Provider response exceeded diagnostic size limit")
            body.extend(chunk)
        item: dict[str, Any] = {"http_status": response.status_code}
        try:
            data = json.loads(body)
            if response.is_error:
                error = data.get("error", {})
                item["error_code"] = self._redact(error.get("code", ""))
                item["error_message"] = self._redact(error.get("message", ""))
                failed = error.get("failed_generation")
                if isinstance(failed, str):
                    try:
                        generated = json.loads(failed)
                        if isinstance(generated, dict):
                            item["generated_section_names"] = [
                                name
                                for name in AnalystSections.model_fields
                                if name in (generated.get("sections") or {})
                            ]
                    except (ValueError, TypeError):
                        item["failed_generation_is_json"] = False
            else:
                usage = data.get("usage", {})
                item["usage"] = {
                    name: usage[name]
                    for name in ("prompt_tokens", "completion_tokens", "total_tokens")
                    if isinstance(usage.get(name), int)
                }
        except (ValueError, AttributeError):
            item["diagnostic"] = "Non-JSON provider response"
        self.results.append(item)
        headers = dict(response.headers)
        headers.pop("content-encoding", None)
        headers.pop("content-length", None)
        await response.aclose()
        return httpx.Response(response.status_code, headers=headers, content=bytes(body))

    async def aclose(self) -> None:
        await self.inner.aclose()


class SyntheticSession:
    async def __aenter__(self) -> SyntheticSession:
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None


async def check(complete_day: bool) -> bool:
    settings = Settings()
    if settings.analyst_api_url != "https://api.groq.com/openai/v1/chat/completions":
        print("No request sent: configure the Groq Chat Completions HTTPS endpoint.")
        return False
    key, token = settings.analyst_api_key, settings.analyst_access_token
    if not key or not key.get_secret_value().startswith("gsk_") or not token:
        print("No request sent: a Groq API key and separate report token are required.")
        return False
    start, end = period_bounds(date(2026, 10, 25), "day", "Asia/Kuala_Lumpur")
    evaluated = end if complete_day else start + timedelta(minutes=30)
    rows = [
        {
            "device_id": device["id"],
            "state": "off" if name == "air_conditioner" else "on",
            "mode": "auto",
            "first_observed_at": start,
            "last_observed_at": evaluated,
            "quality": [],
            "freshness_limit_seconds": 15,
        }
        for name, device in LOCAL_RELAY_DEVICES.items()
    ]
    report = build_analytics_report(
        history=RuntimeHistory(True, start, rows, "Asia/Kuala_Lumpur"),
        classroom_id=LOCAL_CLASSROOM_ID,
        start=start,
        end=end,
        now=evaluated,
        period="day",
        powers={
            "lighting": settings.analytics_light_power_w,
            "fan": settings.analytics_fan_power_w,
            "air_conditioner": settings.analytics_ac_power_w,
        },
    )
    context = {
        "environment": {
            "valid_sample_count": 6,
            "temperature_c": {"average": 26, "minimum": 25.5, "maximum": 26.5},
            "relative_humidity_pct": {"average": 55},
        },
        "occupancy": {
            "valid_sample_count": 6,
            "person_count": {"average": 2, "minimum": 2, "maximum": 2},
        },
        "limitations": ["Synthetic integration-test observations only; no classroom data."],
    }
    transport = DiagnosticTransport(settings)
    app = FastAPI()
    app.include_router(analytics.router)
    app.state.analytics_settings = settings
    app.state.environmental_analyst = EnvironmentalAnalyst(settings, transport=transport)
    app.state.sessions = SyntheticSession
    path = f"/api/v1/classrooms/{LOCAL_CLASSROOM_ID}/analytics"
    started = time.monotonic()
    with (
        patch.object(analytics, "get_analytics_report", AsyncMock(return_value=report)),
        patch.object(analytics, "load_analyst_context", AsyncMock(return_value=context)),
        patch.object(
            analytics,
            "get_historical_comparison",
            AsyncMock(return_value={"available": False, "reason": "Synthetic test."}),
        ),
    ):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, client=("127.0.0.1", 1234)),
            base_url="http://isolated-test",
        ) as client:
            rejected = await client.post(f"{path}/report", json={})
            if rejected.status_code != 401 or transport.results:
                print("Authentication check failed; no authorized request sent.")
                return False
            result = await client.post(
                f"{path}/report",
                json={},
                headers={"Authorization": f"Bearer {token.get_secret_value()}"},
            )
            body = result.json()
            snapshot_ok: bool | None = None
            if body.get("status") == "ready":
                snapshot_ok = body.get("metrics_as_of") in {
                    report.as_of.isoformat().replace("+00:00", "Z"),
                    report.as_of.isoformat(),
                }
            metrics = await client.get(path)
            cached: bool | None = None
            cache_enabled = settings.analyst_cache_ttl_seconds > 0
            if body.get("status") == "ready" and cache_enabled:
                calls = len(transport.results)
                second = await client.post(
                    f"{path}/report",
                    json={},
                    headers={"Authorization": f"Bearer {token.get_secret_value()}"},
                )
                cached = second.json().get("cached") is True and len(transport.results) == calls
    status = {
        "test_data": "synthetic full day" if complete_day else "synthetic partial day",
        "route": "real report handler with simulated storage; no operational server started",
        "provider": "live Groq HTTPS",
        "model": settings.analyst_model,
        "elapsed_seconds": round(time.monotonic() - started, 2),
        "provider_responses": transport.results,
        "report_http_status": result.status_code,
        "analyst_status": body.get("status"),
        "retry_after_seconds": body.get("retry_after_seconds"),
        "report_token_accepted": result.status_code == 200,
        "missing_token_rejected": rejected.status_code == 401,
        "validated_sections": list(body.get("sections") or {}),
        "numerical_snapshot_preserved": snapshot_ok,
        "metrics_endpoint_available": metrics.status_code == 200,
        "cache_verified_without_another_provider_call": cached if cache_enabled else None,
    }
    print(json.dumps(status))
    return (
        body.get("status") == "ready"
        and snapshot_ok is True
        and (cached is True or not cache_enabled)
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="Send a real Groq API request")
    parser.add_argument("--complete-day", action="store_true", help="Use a full 24-hour fixture")
    args = parser.parse_args()
    if not args.live:
        print("No request sent. Add --live to use the configured key with synthetic metrics.")
        return 0
    try:
        return 0 if asyncio.run(check(args.complete_day)) else 1
    except ValidationError:
        print("Configuration/response validation failed. Secret values are not displayed.")
    except Exception as error:
        print(json.dumps({"test_failed": True, "error_class": type(error).__name__}))
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
