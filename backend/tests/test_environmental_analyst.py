"""Provider simulations: no external requests, credentials or physical controls."""

import asyncio
import hashlib
import json
from datetime import UTC, date, datetime, timedelta
from email.utils import format_datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest

from app.core.config import Settings
from app.domain.local_hardware import LOCAL_CLASSROOM_ID, LOCAL_RELAY_DEVICES
from app.domain.runtime import period_bounds
from app.repositories.runtime_history import RuntimeHistory
from app.services import environmental_analyst as analyst_module
from app.services.energy_analytics import build_analytics_report
from app.services.environmental_analyst import EnvironmentalAnalyst

API_SECRET = "provider-secret-not-for-browser"
ACCESS_SECRET = "private-local-analyst-token"
CONTEXT = {
    "environment": {"valid_sample_count": 3, "temperature_c": {"average": 27.2}},
    "occupancy": {"valid_sample_count": 0},
    "limitations": ["Occupancy samples are missing; absence does not establish vacancy."],
}
SECTIONS = {
    "executive_summary": "The available observations are incomplete.",
    "environmental_analysis": "Use the supplied temperature sample statistics cautiously.",
    "device_runtime_analysis": "Unknown periods are excluded from estimated device runtime.",
    "energy_analysis": "Energy estimates use assumed watts and a theoretical baseline.",
    "automation_observations": "Manual requests do not establish an automation failure.",
    "recommendations": ["Collect more observations before comparing full days."],
    "data_quality_and_limitations": "There is no calibrated electricity measurement.",
}


@pytest.fixture(autouse=True)
def reset_process_admission():
    analyst_module._requests.clear()
    analyst_module._in_flight = False
    yield
    analyst_module._requests.clear()
    analyst_module._in_flight = False


def settings(**changes):
    return Settings(
        _env_file=None,
        database_url="postgresql+asyncpg://unused",
        **{
            "analyst_api_key": API_SECRET,
            "analyst_access_token": ACCESS_SECRET,
            "analyst_model": "test-structured-model",
            "analyst_max_retries": 0,
            **changes,
        },
    )


def report(*, powers=None, period="day"):
    start, end = period_bounds(date(2026, 10, 25), period, "Asia/Kuala_Lumpur")
    return build_analytics_report(
        history=RuntimeHistory(True, None, [], "Asia/Kuala_Lumpur"),
        classroom_id=LOCAL_CLASSROOM_ID,
        start=start,
        end=end,
        now=end,
        period=period,
        powers=powers or {"lighting": 10, "fan": 60, "air_conditioner": 1000},
    )


def observed_report(*, finished=True, unknown_seconds=(0, 0, 0)):
    start, end = period_bounds(date(2026, 10, 25), "day", "Asia/Kuala_Lumpur")
    now = end if finished else start + timedelta(seconds=65716)
    rows = [
        {
            "device_id": LOCAL_RELAY_DEVICES[key]["id"],
            "state": "on",
            "first_observed_at": start + timedelta(seconds=unknown),
            "last_observed_at": now,
            "mode": "auto",
            "quality": [],
        }
        for key, unknown in zip(
            ("lighting", "fan", "air_conditioner"), unknown_seconds, strict=True
        )
    ]
    return build_analytics_report(
        history=RuntimeHistory(
            True, min(row["first_observed_at"] for row in rows), rows, "Asia/Kuala_Lumpur"
        ),
        classroom_id=LOCAL_CLASSROOM_ID,
        start=start,
        end=end,
        now=now,
        period="day",
        powers={"lighting": 10, "fan": 60, "air_conditioner": 1000},
    )


def completion(request, *, change=None):
    payload = json.loads(request.content)
    user = json.loads(payload["messages"][1]["content"])
    content = {
        "metrics_digest": user["metrics_digest"],
        "energy_basis": "assumed_wattage_estimate",
        "sections": dict(SECTIONS),
    }
    if change:
        change(content)
    return {"choices": [{"finish_reason": "stop", "message": {"content": json.dumps(content)}}]}


async def test_valid_report_uses_deterministic_summary_and_keeps_credentials_server_side():
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json=completion(request))

    numerical = report()
    service = EnvironmentalAnalyst(settings(), transport=httpx.MockTransport(handler))
    result = await service.generate(numerical, CONTEXT)
    assert result.status == "ready"
    assert result.metrics_as_of == numerical.as_of
    assert result.generated_at.utcoffset().total_seconds() == 0
    assert CONTEXT["limitations"][0] in result.mandatory_disclosures
    assert numerical.limitations[0] in result.mandatory_disclosures
    request = requests[0]
    assert request.headers["Authorization"] == f"Bearer {API_SECRET}"
    payload = json.loads(request.content)
    assert payload["store"] is False
    assert payload["max_completion_tokens"] <= 6000
    assert "tools" not in payload
    assert payload["response_format"]["json_schema"]["strict"] is True
    summary = json.loads(payload["messages"][1]["content"])["summary"]
    assert summary["total_energy"] == {
        **numerical.total_energy.model_dump(mode="json"),
        "provisional_difference_pct": None,
        "final_savings_pct": None,
    }
    assert summary["devices"][0]["runtime"] == numerical.devices[0].runtime.model_dump(mode="json")
    assert str(numerical.classroom_id) not in request.content.decode()
    assert str(numerical.devices[0].device_id) not in request.content.decode()
    assert API_SECRET not in request.content.decode()
    assert API_SECRET not in result.model_dump_json()
    assert ACCESS_SECRET not in result.model_dump_json()
    assert "context" not in result.model_dump()


@pytest.mark.parametrize("changes", [{"analyst_api_key": None}, {"analyst_model": ""}])
async def test_missing_key_or_model_never_calls_network(changes):
    handler = AsyncMock()
    result = await EnvironmentalAnalyst(
        settings(**changes), transport=httpx.MockTransport(handler)
    ).generate(report(), CONTEXT)
    assert result.status == "unavailable"
    handler.assert_not_called()


async def test_disabled_provider_never_calls_network():
    handler = AsyncMock()
    result = await EnvironmentalAnalyst(
        settings(analyst_provider="unsupported"), transport=httpx.MockTransport(handler)
    ).generate(report(), CONTEXT)
    assert result.status == "unavailable"
    handler.assert_not_called()


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://provider.example/v1",
        "https://user:password@provider.example/v1",
        "https://provider.example/v1?key=secret",
        "https://provider.example/v1#fragment",
        "https://[",
        "https://provider.example:invalid/v1",
    ],
)
async def test_invalid_optional_endpoint_disables_ai_without_network(endpoint):
    handler = AsyncMock()
    # Bypass settings validation so this also verifies adapter isolation when
    # configuration originates from a different settings implementation.
    configured = settings().model_copy(update={"analyst_api_url": endpoint})
    result = await EnvironmentalAnalyst(
        configured, transport=httpx.MockTransport(handler)
    ).generate(report(), CONTEXT)
    assert result.status == "unavailable"
    handler.assert_not_called()


@pytest.mark.parametrize(
    "case",
    [
        "invalid_json",
        "list",
        "no_choices",
        "null_choice",
        "null_message",
        "truncated",
        "refusal",
        "tool_call",
        "malformed_content",
        "wrong_digest",
        "measured_basis",
        "extra_control",
        "numeric_section",
    ],
)
async def test_malformed_refused_or_unbound_responses_are_unavailable_and_uncached(case):
    calls = 0

    def handler(request):
        nonlocal calls
        calls += 1
        response = completion(request)
        message = response["choices"][0]["message"]
        if case == "invalid_json":
            return httpx.Response(200, content=b"not JSON")
        if case == "list":
            response = []
        elif case == "no_choices":
            response = {}
        elif case == "null_choice":
            response["choices"] = [None]
        elif case == "null_message":
            response["choices"][0]["message"] = None
        elif case == "truncated":
            response["choices"][0]["finish_reason"] = "length"
        elif case == "refusal":
            message["refusal"] = "Refused"
        elif case == "tool_call":
            message["tool_calls"] = [{"function": {"name": "operate_relay"}}]
        elif case == "malformed_content":
            message["content"] = "incomplete"
        else:
            content = json.loads(message["content"])
            if case == "wrong_digest":
                content["metrics_digest"] = "0" * 64
            elif case == "measured_basis":
                content["energy_basis"] = "measured"
            elif case == "extra_control":
                content["command"] = {"on": True}
            elif case == "numeric_section":
                content["sections"]["executive_summary"] = 123
            message["content"] = json.dumps(content)
        return httpx.Response(200, json=response)

    service = EnvironmentalAnalyst(settings(), transport=httpx.MockTransport(handler))
    for _ in range(2):
        result = await service.generate(report(), CONTEXT)
        assert result.status == "unavailable"
        assert result.sections is None
    assert calls == 2


async def test_oversized_response_is_rejected():
    result = await EnvironmentalAnalyst(
        settings(),
        transport=httpx.MockTransport(lambda _: httpx.Response(200, content=b"x" * 100001)),
    ).generate(report(), CONTEXT)
    assert result.status == "unavailable"


async def test_timeout_and_connection_failure_retry_is_bounded():
    calls = 0
    sleeper = AsyncMock()

    def handler(request):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise httpx.ReadTimeout("sensitive transport detail", request=request)
        if calls == 2:
            raise httpx.ConnectError("sensitive transport detail", request=request)
        return httpx.Response(200, json=completion(request))

    service = EnvironmentalAnalyst(
        settings(analyst_max_retries=2), transport=httpx.MockTransport(handler), sleeper=sleeper
    )
    result = await service.generate(report(), CONTEXT)
    assert result.status == "ready"
    assert calls == 3
    assert [call.args[0] for call in sleeper.await_args_list] == [0.5, 1.0]


async def test_hanging_provider_is_cancelled_by_deadline_and_releases_admission():
    calls = 0
    cancelled = False

    async def handler(request):
        nonlocal calls, cancelled
        calls += 1
        if calls == 1:
            try:
                await asyncio.Future()
            finally:
                cancelled = True
        return httpx.Response(200, json=completion(request))

    service = EnvironmentalAnalyst(
        settings(analyst_timeout_seconds=1), transport=httpx.MockTransport(handler)
    )
    result = await asyncio.wait_for(service.generate(report(), CONTEXT), timeout=2)
    assert result.status == "unavailable"
    assert cancelled
    assert not analyst_module._in_flight
    assert (await service.generate(report(), CONTEXT)).status == "ready"


async def test_transient_server_error_retries_but_does_not_cache_failure():
    calls = 0
    sleeper = AsyncMock()

    def handler(request):
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(503, json={"error": "upstream unavailable"})
        return httpx.Response(200, json=completion(request))

    service = EnvironmentalAnalyst(
        settings(analyst_max_retries=1),
        transport=httpx.MockTransport(handler),
        sleeper=sleeper,
    )
    assert (await service.generate(report(), CONTEXT)).status == "ready"
    assert calls == 2
    assert (await service.generate(report(), CONTEXT)).cached
    assert calls == 2


async def test_provider_rate_limit_preserves_cooldown_without_immediate_retry():
    calls = 0
    sleeper = AsyncMock()

    def handler(_request):
        nonlocal calls
        calls += 1
        return httpx.Response(429, headers={"Retry-After": "999"}, json={"key": API_SECRET})

    result = await EnvironmentalAnalyst(
        settings(analyst_max_retries=1), transport=httpx.MockTransport(handler), sleeper=sleeper
    ).generate(report(), CONTEXT)
    assert result.status == "rate_limited"
    assert calls == 1
    sleeper.assert_not_awaited()
    assert result.retry_after_seconds == 999
    assert API_SECRET not in result.model_dump_json()


@pytest.mark.parametrize("header,expected", [("24.4875", 25), ("invalid", 25), (None, 25)])
async def test_groq_cooldown_header_or_error_message_is_used_without_exposing_details(
    header, expected
):
    headers = {"Retry-After": header} if header else {}
    handler = AsyncMock(
        return_value=httpx.Response(
            429,
            headers=headers,
            json={
                "error": {"message": f"Sensitive {API_SECRET}; Please try again in 24.4875s."},
            },
        )
    )
    service = EnvironmentalAnalyst(
        settings(analyst_max_retries=2), transport=httpx.MockTransport(handler)
    )
    result = await service.generate(report(), CONTEXT)
    assert result.status == "rate_limited"
    assert result.retry_after_seconds == expected
    assert handler.await_count == 1
    assert API_SECRET not in result.model_dump_json()


async def test_rate_limit_without_cooldown_uses_one_minute_and_does_not_retry():
    handler = AsyncMock(return_value=httpx.Response(429, json={"error": "limited"}))
    service = EnvironmentalAnalyst(
        settings(analyst_max_retries=2), transport=httpx.MockTransport(handler)
    )
    result = await service.generate(report(), CONTEXT)
    assert result.retry_after_seconds == 60
    assert handler.await_count == 1


def test_retry_after_http_date_preserves_provider_deadline(monkeypatch):
    now = datetime(2026, 10, 26, 8, tzinfo=UTC)
    monkeypatch.setattr(analyst_module, "datetime", SimpleNamespace(now=lambda _zone: now))
    header = format_datetime(now + timedelta(seconds=25), usegmt=True)
    response = httpx.Response(429, headers={"Retry-After": header})
    assert analyst_module._rate_limit_delay(response, b"{}") == 25


async def test_rate_limit_cooldown_blocks_refresh_but_allows_cached_report():
    now = [1000.0]
    calls = 0

    def handler(request):
        nonlocal calls
        calls += 1
        if calls == 2:
            return httpx.Response(429, headers={"Retry-After": "25"})
        return httpx.Response(200, json=completion(request))

    service = EnvironmentalAnalyst(
        settings(), transport=httpx.MockTransport(handler), clock=lambda: now[0]
    )
    assert (await service.generate(report(), CONTEXT)).status == "ready"
    assert (await service.generate(report(), CONTEXT, refresh=True)).retry_after_seconds == 25
    assert (await service.generate(report(), CONTEXT)).cached
    assert (await service.generate(report(), CONTEXT, refresh=True)).retry_after_seconds == 25
    assert calls == 2
    now[0] += 26
    assert (await service.generate(report(), CONTEXT, refresh=True)).status == "ready"
    assert calls == 3


def test_identical_hourly_buckets_compress_losslessly_and_keep_numerical_totals():
    numerical = report()
    summary = analyst_module._summary(numerical, CONTEXT)
    for device, supplied in zip(numerical.devices, summary["devices"], strict=True):
        assert len(device.hourly) == 24
        assert len(supplied["hourly"]) == 1
        group = supplied["hourly"][0]
        assert group["bucket_count"] == 24
        assert group["start"] == device.hourly[0].start.isoformat()
        assert group["end"] == device.hourly[-1].end.isoformat()
        for state in ("on", "off", "unknown"):
            assert sum(
                row[f"{state}_seconds_per_bucket"] * row["bucket_count"]
                for row in supplied["hourly"]
            ) == getattr(device.runtime, f"{state}_seconds")
        assert supplied["runtime"] == device.runtime.model_dump(mode="json")
        assert supplied["energy"] == {
            **device.energy.model_dump(mode="json"),
            "provisional_difference_pct": None,
            "final_savings_pct": None,
        }


def test_compaction_preserves_runtime_changes_activations_and_partial_hours():
    numerical = report()
    buckets = [bucket.model_copy(deep=True) for bucket in numerical.devices[0].hourly[:4]]
    for bucket in buckets[1:]:
        bucket.on_seconds = 3600
        bucket.unknown_seconds = 0
        bucket.coverage_pct = 100
    buckets[1].activations = 1
    buckets[1].transitions = 1
    buckets[-1].end = buckets[-1].start + timedelta(minutes=30)
    buckets[-1].on_seconds = 1800
    buckets[-1].elapsed_seconds = 1800
    groups = analyst_module._buckets(buckets, 48)
    assert len(groups) == 4
    assert groups[0]["unknown_seconds_per_bucket"] == 3600
    assert groups[1]["activations_per_bucket"] == 1
    assert groups[-1]["bucket_seconds"] == 1800
    assert groups[-1]["on_seconds_per_bucket"] == 1800


def test_compaction_does_not_merge_noncontiguous_buckets():
    buckets = report().devices[0].hourly
    groups = analyst_module._buckets([buckets[0], buckets[2]], 48)
    assert len(groups) == 2
    assert all(group["bucket_count"] == 1 for group in groups)


async def test_unauthorized_provider_failure_is_sanitized_and_not_retried():
    handler = AsyncMock(return_value=httpx.Response(401, json={"error": API_SECRET}))
    result = await EnvironmentalAnalyst(
        settings(analyst_max_retries=2), transport=httpx.MockTransport(handler)
    ).generate(report(), CONTEXT)
    assert result.status == "unavailable"
    assert API_SECRET not in result.model_dump_json()
    assert handler.await_count == 1


async def test_cache_returns_original_metrics_timestamp_despite_live_data_and_rate_cap():
    calls = 0

    def handler(request):
        nonlocal calls
        calls += 1
        return httpx.Response(200, json=completion(request))

    service = EnvironmentalAnalyst(
        settings(analyst_requests_per_minute=1), transport=httpx.MockTransport(handler)
    )
    initial = report()
    first = await service.generate(initial, CONTEXT)
    newer = initial.model_copy(update={"as_of": initial.as_of + timedelta(seconds=5)})
    cached = await service.generate(newer, CONTEXT)
    assert cached.cached
    assert cached.metrics_as_of == initial.as_of
    assert cached.summary_hash == first.summary_hash
    assert calls == 1
    limited = await service.generate(newer, CONTEXT, refresh=True)
    assert limited.status == "rate_limited"
    assert limited.retry_after_seconds <= 60


async def test_refresh_bypasses_valid_cache_and_cache_ttl_expires():
    calls = 0
    now = [1000.0]

    def handler(request):
        nonlocal calls
        calls += 1
        return httpx.Response(200, json=completion(request))

    service = EnvironmentalAnalyst(
        settings(analyst_cache_ttl_seconds=10),
        transport=httpx.MockTransport(handler),
        clock=lambda: now[0],
    )
    assert (await service.generate(report(), CONTEXT)).status == "ready"
    refreshed = await service.generate(report(), CONTEXT, refresh=True)
    assert not refreshed.cached
    assert calls == 2
    now[0] += 11
    assert not (await service.generate(report(), CONTEXT)).cached
    assert calls == 3


@pytest.mark.parametrize("dimension", ["power", "classroom", "method", "freshness"])
async def test_cache_identity_cannot_collide_across_report_assumptions(dimension):
    calls = 0

    def handler(request):
        nonlocal calls
        calls += 1
        return httpx.Response(200, json=completion(request))

    service = EnvironmentalAnalyst(settings(), transport=httpx.MockTransport(handler))
    first = report()
    await service.generate(first, CONTEXT)
    second = {
        "power": report(powers={"lighting": 20, "fan": 60, "air_conditioner": 1000}),
        "classroom": first.model_copy(update={"classroom_id": uuid4()}),
        "method": first.model_copy(update={"methodology_version": "changed"}),
        "freshness": first.model_copy(update={"stale_after_seconds": 30}),
    }[dimension]
    result = await service.generate(second, CONTEXT)
    assert result.status == "ready"
    assert not result.cached
    assert calls == 2


async def test_process_global_cap_applies_across_service_instances():
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json=completion(request)))
    first = EnvironmentalAnalyst(settings(analyst_requests_per_minute=1), transport=transport)
    second = EnvironmentalAnalyst(settings(analyst_requests_per_minute=1), transport=transport)
    assert (await first.generate(report(), CONTEXT)).status == "ready"
    assert (await second.generate(report(), CONTEXT)).status == "rate_limited"
    assert (await first.generate(report(), CONTEXT)).cached


async def test_concurrent_generation_is_rejected_without_wait_queue():
    entered = asyncio.Event()
    release = asyncio.Event()

    async def handler(request):
        entered.set()
        await release.wait()
        return httpx.Response(200, json=completion(request))

    service = EnvironmentalAnalyst(settings(), transport=httpx.MockTransport(handler))
    running = asyncio.create_task(service.generate(report(), CONTEXT))
    await entered.wait()
    try:
        result = await asyncio.wait_for(service.generate(report(), CONTEXT), 0.5)
        assert result.status == "rate_limited"
    finally:
        release.set()
        await running


async def test_weekly_monthly_summaries_omit_hourly_samples_and_keep_aggregate_metrics():
    captured = []

    def handler(request):
        captured.append(
            json.loads(json.loads(request.content)["messages"][1]["content"])["summary"]
        )
        return httpx.Response(200, json=completion(request))

    context = {
        **CONTEXT,
        "environment": {
            **CONTEXT["environment"],
            "hourly": [{"sample_count": 1}] * 744,
        },
    }
    service = EnvironmentalAnalyst(settings(), transport=httpx.MockTransport(handler))
    for period in ("week", "month"):
        numerical = report(period=period)
        assert (await service.generate(numerical, context)).status == "ready"
        summary = captured[-1]
        assert "hourly" not in summary["context"]["environment"]
        assert summary["context"]["environment"]["temperature_c"]["average"] == 27.2
        assert all(device["hourly"] == [] for device in summary["devices"])
        assert summary["total_energy"] == {
            **numerical.total_energy.model_dump(mode="json"),
            "provisional_difference_pct": None,
            "final_savings_pct": None,
        }


async def test_context_filters_identifiers_and_credentials_and_provider_cannot_echo_secrets():
    captured = []

    def handler(request):
        captured.append(request.content.decode())
        return httpx.Response(
            200,
            json=completion(
                request,
                change=lambda content: content["sections"].update(executive_summary=API_SECRET),
            ),
        )

    context = {
        **CONTEXT,
        "api_key": API_SECRET,
        "camera_frame": "image-bytes",
        "environment": {"sensor_id": str(uuid4()), "token": ACCESS_SECRET},
    }
    result = await EnvironmentalAnalyst(
        settings(), transport=httpx.MockTransport(handler)
    ).generate(report(), context)
    assert result.status == "unavailable"
    assert API_SECRET not in result.model_dump_json()
    assert API_SECRET not in captured[0]
    assert ACCESS_SECRET not in captured[0]
    assert "image-bytes" not in captured[0]


async def test_cache_memory_is_bounded_to_64_reports():
    now = [1000.0]
    service = EnvironmentalAnalyst(
        settings(analyst_requests_per_minute=30, analyst_cache_ttl_seconds=3600),
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json=completion(request))
        ),
        clock=lambda: now[0],
    )
    numerical = report()
    for index in range(65):
        if index and index % 29 == 0:
            now[0] += 61
        result = await service.generate(
            numerical.model_copy(update={"classroom_id": uuid4()}), CONTEXT
        )
        assert result.status == "ready"
    assert len(service._cache) == 64


@pytest.mark.parametrize("case", ["no_history", "partial_coverage", "unfinished_full_coverage"])
async def test_incomplete_commentary_leads_with_coverage_and_withholds_comparison_percentages(case):
    numerical = {
        "no_history": report(),
        "partial_coverage": observed_report(
            finished=False, unknown_seconds=(62633, 62618, 62631)
        ),
        "unfinished_full_coverage": observed_report(finished=False),
    }[case]
    original = numerical.model_dump_json()
    captured = []

    def handler(request):
        captured.append(json.loads(json.loads(request.content)["messages"][1]["content"]))
        return httpx.Response(
            200,
            json=completion(
                request,
                change=lambda content: content["sections"].update(
                    executive_summary="The system achieved 99% savings.",
                    energy_analysis="Savings reached 99% against the baseline.",
                ),
            ),
        )

    result = await EnvironmentalAnalyst(
        settings(), transport=httpx.MockTransport(handler)
    ).generate(numerical, CONTEXT)
    assert result.status == "ready"
    assert result.sections.executive_summary.startswith("Data completeness:")
    assert "coverage of the elapsed period" in result.sections.executive_summary
    assert "99%" not in result.sections.model_dump_json()
    assert "Comparison percentages are withheld" in result.sections.energy_analysis
    assert "savings cannot be established" in result.sections.energy_analysis
    if case == "unfinished_full_coverage":
        assert "period is still in progress" in result.sections.executive_summary
        assert "lighting 100.00%" in result.sections.executive_summary
    elif case == "partial_coverage":
        assert "lighting 4.69%" in result.sections.executive_summary
    summary = captured[0]["summary"]
    assert summary["commentary_policy"]["comparison_percentages_withheld"] is True
    assert summary["total_energy"]["provisional_difference_pct"] is None
    assert all(device["energy"]["provisional_difference_pct"] is None
               for device in summary["devices"])
    bound = hashlib.sha256(json.dumps(
        summary, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode()).hexdigest()
    assert result.summary_hash == captured[0]["metrics_digest"] == bound
    assert numerical.model_dump_json() == original


async def test_server_runtime_paragraph_keeps_unknown_time_separate_per_device():
    numerical = observed_report(finished=False, unknown_seconds=(62633, 62618, 62631))
    result = await EnvironmentalAnalyst(
        settings(),
        transport=httpx.MockTransport(lambda request: httpx.Response(
            200,
            json=completion(
                request,
                change=lambda content: content["sections"].update(
                    device_runtime_analysis="UNKNOWN time totals 62633 seconds across all devices."
                ),
            ),
        )),
    ).generate(numerical, CONTEXT)
    assert result.status == "ready"
    paragraph = result.sections.device_runtime_analysis
    assert "Lighting: recorded ON 3083 s; OFF 0 s; UNKNOWN 62633 s" in paragraph
    assert "Fan: recorded ON 3098 s; OFF 0 s; UNKNOWN 62618 s" in paragraph
    assert "Air conditioner: recorded ON 3085 s; OFF 0 s; UNKNOWN 62631 s" in paragraph
    assert "UNKNOWN time is reported separately for each device" in paragraph
    assert "across all devices" not in paragraph


@pytest.mark.parametrize("finished", [False, True])
@pytest.mark.parametrize("field", list(SECTIONS))
async def test_unsupported_savings_claims_in_any_section_get_safe_fallback(field, finished):
    claim = "The automation achieved 99% energy savings and verified lower electricity usage."

    def change(content):
        content["sections"][field] = [claim] if field == "recommendations" else claim

    result = await EnvironmentalAnalyst(
        settings(),
        transport=httpx.MockTransport(lambda request: httpx.Response(
            200, json=completion(request, change=change)
        )),
    ).generate(observed_report(finished=finished), CONTEXT)
    assert result.status == "ready"
    assert "99%" not in result.sections.model_dump_json()
    assert "achieved" not in result.sections.model_dump_json()
    assert result.sections.recommendations


async def test_valid_humidity_percentage_survives_in_environmental_interpretation():
    text = "Accepted humidity samples averaged 61.58%; these samples do not cover every minute."
    result = await EnvironmentalAnalyst(
        settings(),
        transport=httpx.MockTransport(lambda request: httpx.Response(
            200,
            json=completion(
                request,
                change=lambda content: content["sections"].update(environmental_analysis=text),
            ),
        )),
    ).generate(report(), CONTEXT)
    assert result.status == "ready"
    assert result.sections.environmental_analysis == text


async def test_complete_report_retains_supplied_theoretical_comparison_and_coverage_lead():
    numerical = observed_report()
    summary = analyst_module._summary(numerical, CONTEXT)
    assert summary["commentary_policy"]["comparison_percentages_withheld"] is False
    assert summary["total_energy"] == numerical.total_energy.model_dump(mode="json")
    result = await EnvironmentalAnalyst(
        settings(),
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json=completion(request))
        ),
    ).generate(numerical, CONTEXT)
    assert result.status == "ready"
    assert result.sections.executive_summary.startswith(
        "Data completeness: the reporting period has ended with complete runtime coverage."
    )
    assert "final theoretical difference is 0.00%" in result.sections.energy_analysis
    assert "not measured or verified savings" in result.sections.energy_analysis


def test_incomplete_provider_context_hides_historical_comparison_percentages_only():
    numerical = observed_report(finished=False)
    context = {
        **CONTEXT,
        "historical_comparison": {
            "previous_total_energy": observed_report().total_energy.model_dump(mode="json"),
            "both_periods_complete": False,
        },
    }
    original = json.dumps(context, sort_keys=True)
    summary = analyst_module._summary(numerical, context)
    previous = summary["context"]["historical_comparison"]["previous_total_energy"]
    assert previous["provisional_difference_pct"] is None
    assert previous["final_savings_pct"] is None
    assert previous["estimated_energy_kwh"] == 25.68
    assert json.dumps(context, sort_keys=True) == original


async def test_commentary_policy_revision_invalidates_older_cached_prose(monkeypatch):
    calls = 0

    def handler(request):
        nonlocal calls
        calls += 1
        return httpx.Response(200, json=completion(request))

    service = EnvironmentalAnalyst(settings(), transport=httpx.MockTransport(handler))
    numerical = report()
    first = await service.generate(numerical, CONTEXT)
    monkeypatch.setattr(analyst_module, "COMMENTARY_VERSION", "coverage_first_next")
    second = await service.generate(numerical, CONTEXT)
    assert first.status == second.status == "ready"
    assert not second.cached
    assert second.summary_hash != first.summary_hash
    assert calls == 2
