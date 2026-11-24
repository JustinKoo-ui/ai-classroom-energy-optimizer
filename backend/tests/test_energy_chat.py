"""Read-only AI chat over server-calculated analytics, with a simulated provider."""

import hashlib
import json
from datetime import date, timedelta

import httpx
import pytest

from app.core.config import Settings
from app.domain.local_hardware import LOCAL_CLASSROOM_ID, LOCAL_RELAY_DEVICES
from app.domain.runtime import period_bounds
from app.repositories.runtime_history import RuntimeHistory
from app.schemas.energy_chat import ChatTurn
from app.services import energy_chat as chat_module
from app.services import environmental_analyst as analyst_module
from app.services.energy_analytics import build_analytics_report
from app.services.energy_chat import OFF_TOPIC_ANSWER, EnergyChat

API_SECRET = "provider-secret-not-for-browser"
ACCESS_SECRET = "private-local-analyst-token"
CONTEXT = {
    "environment": {"valid_sample_count": 3, "temperature_c": {"average": 27.2}},
    "occupancy": {"valid_sample_count": 0},
    "limitations": ["Occupancy samples are missing; absence does not establish vacancy."],
}
ANSWER = "The air conditioner was recorded ON for the whole elapsed day."


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
            "analyst_requests_per_minute": 30,
            **changes,
        },
    )


def report(*, finished=True):
    start, end = period_bounds(date(2026, 10, 25), "day", "Asia/Kuala_Lumpur")
    now = end if finished else start + timedelta(hours=10)
    rows = [
        {
            "device_id": LOCAL_RELAY_DEVICES[key]["id"],
            "state": "on",
            "first_observed_at": start,
            "last_observed_at": now,
            "mode": "auto",
            "quality": [],
        }
        for key in ("lighting", "fan", "air_conditioner")
    ]
    return build_analytics_report(
        history=RuntimeHistory(True, start, rows, "Asia/Kuala_Lumpur"),
        classroom_id=LOCAL_CLASSROOM_ID,
        start=start,
        end=end,
        now=now,
        period="day",
        powers={"lighting": 10, "fan": 60, "air_conditioner": 1000},
    )


def data_block(request):
    system = json.loads(request.content)["messages"][0]["content"]
    return json.loads(system.split("\nDATA:\n", 1)[1])


def completion(request, *, change=None):
    content = {
        "metrics_digest": data_block(request)["metrics_digest"],
        "on_topic": True,
        "answer": ANSWER,
        "suggested_questions": ["Which appliance used the most estimated energy?"],
    }
    if change:
        change(content)
    return {"choices": [{"finish_reason": "stop", "message": {"content": json.dumps(content)}}]}


def provider(seen, **kwargs):
    def handler(request):
        seen.append(request)
        return httpx.Response(200, json=completion(request, **kwargs))

    return httpx.MockTransport(handler)


async def ask(service, question="Which appliance ran longest?", history=None, data=None):
    return await service.answer(data or report(), CONTEXT, question, history or [])


async def test_answer_is_grounded_in_server_data_and_keeps_credentials_server_side():
    seen = []
    service = EnergyChat(settings(), transport=provider(seen))
    history = [
        ChatTurn(role="user", content="How much energy today?"),
        ChatTurn(role="assistant", content="About 25 kWh estimated."),
    ]
    result = await ask(service, history=history)

    assert result.status == "ready"
    assert result.answer == ANSWER
    assert result.suggested_questions == ["Which appliance used the most estimated energy?"]
    assert result.data_complete is True
    assert result.period == "day"
    request = seen[0]
    assert request.headers["Authorization"] == f"Bearer {API_SECRET}"
    body = json.loads(request.content)
    assert API_SECRET not in request.content.decode()
    assert ACCESS_SECRET not in request.content.decode()
    assert body["store"] is False
    assert body["response_format"]["json_schema"]["strict"] is True
    assert "tools" not in body
    roles = [message["role"] for message in body["messages"]]
    assert roles == ["system", "user", "assistant", "user"]
    assert body["messages"][-1]["content"] == "Which appliance ran longest?"
    data = data_block(request)
    summary = analyst_module._json(data["summary"])
    assert data["metrics_digest"] == hashlib.sha256(summary.encode()).hexdigest()
    assert {device["device"] for device in data["summary"]["devices"]} == {
        "lighting",
        "fan",
        "air_conditioner",
    }


async def test_off_topic_questions_get_a_fixed_scope_reply():
    seen = []
    service = EnergyChat(
        settings(),
        transport=provider(
            seen, change=lambda c: c.update(on_topic=False, answer="Here is a poem.")
        ),
    )
    result = await ask(service, question="Write me a poem about cats.")
    assert result.status == "ready"
    assert result.on_topic is False
    assert result.answer == OFF_TOPIC_ANSWER


@pytest.mark.parametrize(
    "change",
    [
        lambda c: c.update(metrics_digest="0" * 64),
        lambda c: c.update(answer=f"Your key is {API_SECRET}"),
        lambda c: c.update(suggested_questions=[f"Use token {ACCESS_SECRET}?"]),
        lambda c: c.update(answer=""),
        lambda c: c.update(extra_field="not allowed"),
        lambda c: c.pop("on_topic"),
    ],
)
async def test_unbound_leaking_or_malformed_replies_are_unavailable(change):
    service = EnergyChat(settings(), transport=provider([], change=change))
    result = await ask(service)
    assert result.status == "unavailable"
    assert result.answer is None
    assert API_SECRET not in result.model_dump_json()
    assert not analyst_module._in_flight


@pytest.mark.parametrize(
    "message",
    [
        {"tool_calls": [{"function": {"name": "set_power"}}], "content": "{}"},
        {"refusal": "no", "content": "{}"},
        {"content": "not json"},
    ],
)
async def test_tool_calls_refusals_and_invalid_json_are_rejected(message):
    def handler(request):
        return httpx.Response(
            200, json={"choices": [{"finish_reason": "stop", "message": message}]}
        )

    result = await ask(EnergyChat(settings(), transport=httpx.MockTransport(handler)))
    assert result.status == "unavailable"


@pytest.mark.parametrize("changes", [{"analyst_api_key": None}, {"analyst_model": " "}])
async def test_unconfigured_provider_never_calls_network(changes):
    seen = []
    result = await ask(EnergyChat(settings(**changes), transport=provider(seen)))
    assert result.status == "unavailable"
    assert seen == []


async def test_provider_rate_limit_sets_cooldown_without_spending_more_requests():
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(429, headers={"Retry-After": "30"}, json={"error": {}})

    now = [1000.0]
    service = EnergyChat(settings(), transport=httpx.MockTransport(handler), clock=lambda: now[0])
    first = await ask(service)
    now[0] += 5
    second = await ask(service)
    assert (first.status, first.retry_after_seconds) == ("rate_limited", 30)
    assert (second.status, second.retry_after_seconds) == ("rate_limited", 25)
    assert len(seen) == 1


async def test_chat_and_report_share_one_process_wide_request_cap():
    seen = []
    service = EnergyChat(settings(analyst_requests_per_minute=2), transport=provider(seen))
    analyst_module._requests.append(service._clock())  # a report already used one slot
    assert (await ask(service)).status == "ready"
    limited = await ask(service)
    assert limited.status == "rate_limited"
    assert limited.retry_after_seconds is not None and limited.retry_after_seconds >= 1
    assert len(seen) == 1


async def test_busy_provider_rejects_without_queueing():
    analyst_module._in_flight = True
    seen = []
    result = await ask(EnergyChat(settings(), transport=provider(seen)))
    assert (result.status, result.retry_after_seconds) == ("rate_limited", 2)
    assert seen == []


async def test_server_error_releases_admission():
    def handler(request):
        return httpx.Response(500, json={"error": {"message": API_SECRET}})

    result = await ask(EnergyChat(settings(), transport=httpx.MockTransport(handler)))
    assert result.status == "unavailable"
    assert API_SECRET not in result.model_dump_json()
    assert not analyst_module._in_flight


async def test_large_context_drops_hourly_series_but_keeps_totals(monkeypatch):
    monkeypatch.setattr(chat_module, "MAX_CONTEXT_BYTES", 500)
    seen = []
    await ask(EnergyChat(settings(), transport=provider(seen)))
    summary = data_block(seen[0])["summary"]
    assert all(device["hourly"] == [] for device in summary["devices"])
    assert all("runtime" in device for device in summary["devices"])
    assert "chat_note" in summary


async def test_small_context_keeps_hourly_series():
    seen = []
    await ask(EnergyChat(settings(), transport=provider(seen)))
    summary = data_block(seen[0])["summary"]
    assert any(device["hourly"] for device in summary["devices"])
    assert "chat_note" not in summary


async def test_incomplete_period_is_flagged_and_withholds_comparison_percentages():
    seen = []
    result = await ask(
        EnergyChat(settings(), transport=provider(seen)), data=report(finished=False)
    )
    assert result.data_complete is False
    total = data_block(seen[0])["summary"]["total_energy"]
    assert total["final_savings_pct"] is None
    assert total["provisional_difference_pct"] is None


async def test_extra_long_or_duplicate_suggestions_are_trimmed_not_fatal():
    many = [
        "Which appliance ran longest?",
        "Which appliance ran longest?",
        "x" * 161,
        "   ",
        "When was the room empty?",
        "How warm was it?",
        "A fourth valid question?",
    ]
    service = EnergyChat(
        settings(), transport=provider([], change=lambda c: c.update(suggested_questions=many))
    )
    result = await ask(service)
    assert result.status == "ready"
    assert result.suggested_questions == [
        "Which appliance ran longest?",
        "When was the room empty?",
        "How warm was it?",
    ]


async def test_provider_errors_are_logged_without_credentials_or_generated_text(caplog):
    def handler(request):
        return httpx.Response(
            400,
            json={
                "error": {
                    "code": "json_validate_failed",
                    "message": "Generated JSON does not match the expected schema.",
                    "failed_generation": f"secret model output {API_SECRET}",
                }
            },
        )

    result = await ask(EnergyChat(settings(), transport=httpx.MockTransport(handler)))
    assert result.status == "unavailable"
    assert "HTTP 400 (json_validate_failed: Generated JSON does not match" in caplog.text
    assert API_SECRET not in caplog.text
    assert "secret model output" not in caplog.text


async def test_truncated_reply_logs_finish_reason_and_token_usage(caplog):
    def handler(request):
        return httpx.Response(
            200,
            json={
                "choices": [{"finish_reason": "length", "message": {"content": '{"a":'}}],
                "usage": {"prompt_tokens": 5100, "completion_tokens": 3000},
            },
        )

    result = await ask(EnergyChat(settings(), transport=httpx.MockTransport(handler)))
    assert result.status == "unavailable"
    assert "finish_reason=length" in caplog.text
    assert "'prompt_tokens': 5100" in caplog.text


async def test_validation_failures_log_field_names_not_generated_text(caplog):
    service = EnergyChat(
        settings(),
        transport=provider([], change=lambda c: c.update(answer="", note="private words")),
    )
    result = await ask(service)
    assert result.status == "unavailable"
    assert "failed validation" in caplog.text
    assert "answer" in caplog.text
    assert "private words" not in caplog.text


async def test_reasoning_effort_is_sent_only_when_configured():
    plain, low = [], []
    await ask(EnergyChat(settings(), transport=provider(plain)))
    await ask(EnergyChat(settings(analyst_reasoning_effort="low"), transport=provider(low)))
    assert "reasoning_effort" not in json.loads(plain[0].content)
    assert json.loads(low[0].content)["reasoning_effort"] == "low"


def test_reasoning_effort_rejects_unknown_values():
    with pytest.raises(ValueError):
        settings(analyst_reasoning_effort="extreme")


@pytest.mark.parametrize(
    ("raw", "clean"),
    [
        ("Lights ran 8 h.\n\n---\n\nAC ran 6 h.", "Lights ran 8 h.\n\nAC ran 6 h."),
        ("A\n***\nB\n___\nC\n= = =\nD", "A\nB\nC\nD"),
        ("## Summary\nThe **fan** ran `7.5 h`.", "Summary\nThe fan ran 7.5 h."),
        ("| Device | Hours |\n|---|:---:|\n| AC | 6.2 |", "Device · Hours\nAC · 6.2"),
        (
            "- Switch the AC off at lunch\n- Use daylight",
            "- Switch the AC off at lunch\n- Use daylight",
        ),
        ("Saving is 5 - 10%, not 5--10%.", "Saving is 5 - 10%, not 5--10%."),
    ],
)
def test_plain_text_strips_markdown_but_keeps_content(raw, clean):
    assert chat_module._plain_text(raw) == clean


async def test_markdown_in_answers_is_cleaned_before_reaching_the_browser():
    service = EnergyChat(
        settings(),
        transport=provider(
            [], change=lambda c: c.update(answer="**Lights**: 8 h\n\n---\n\nAC: 6 h")
        ),
    )
    result = await ask(service)
    assert result.answer == "Lights: 8 h\n\nAC: 6 h"


async def test_low_reasoning_reserves_a_smaller_output_budget():
    default, low = [], []
    await ask(EnergyChat(settings(), transport=provider(default)))
    await ask(EnergyChat(settings(analyst_reasoning_effort="low"), transport=provider(low)))
    assert json.loads(default[0].content)["max_completion_tokens"] == 3000
    assert json.loads(low[0].content)["max_completion_tokens"] == 1500
