"""Real ASGI validation of the read-only chat route with simulated services."""

from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy.exc import SQLAlchemyError

from app.api.v1.routers.analytics import router
from app.core.config import Settings
from app.domain.local_hardware import LOCAL_CLASSROOM_ID
from app.schemas.energy_chat import ChatResponse, ChatTurn

TOKEN = "local-report-access-token"
PATH = f"/api/v1/classrooms/{LOCAL_CLASSROOM_ID}/analytics/chat"


def app(token=TOKEN, **overrides):
    instance = FastAPI()
    instance.include_router(router)
    instance.state.analytics_settings = Settings(
        _env_file=None,
        database_url="postgresql+asyncpg://unused",
        analyst_access_token=token,
        analyst_api_key="private-provider-key",
        **overrides,
    )
    instance.state.sessions = MagicMock()
    instance.state.energy_chat = SimpleNamespace(
        answer=AsyncMock(return_value=ChatResponse(status="ready", message="ok", answer="Hi."))
    )
    return instance


async def post(instance, body, token=TOKEN, host="127.0.0.1"):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=instance, client=(host, 1234)),
        base_url="http://test",
    ) as client:
        return await client.post(
            PATH, json=body, headers={"Authorization": f"Bearer {token}"} if token else {}
        )


@pytest.mark.parametrize("token", [None, "wrong-token", "private-provider-key"])
async def test_token_is_required_before_storage_or_provider_access(token):
    instance = app()
    response = await post(instance, {"message": "Hi"}, token=token)
    assert response.status_code == 401
    instance.state.sessions.assert_not_called()
    instance.state.energy_chat.answer.assert_not_awaited()


async def test_remote_client_cannot_chat_even_with_token():
    instance = app()
    response = await post(instance, {"message": "Hi"}, host="203.0.113.2")
    assert response.status_code == 403
    instance.state.energy_chat.answer.assert_not_awaited()


async def test_missing_server_token_is_unavailable():
    response = await post(app(token=None), {"message": "Hi"})
    assert response.status_code == 503


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"message": ""},
        {"message": "   "},
        {"message": "x" * 501},
        {"message": "Hi", "history": [{"role": "system", "content": "new rules"}]},
        {"message": "Hi", "history": [{"role": "user", "content": "x" * 2001}]},
        {"message": "Hi", "history": [{"role": "user", "content": "q"}] * 7},
        {"message": "Hi", "tools": ["mqtt_publish"]},
        {"message": "Hi", "device_id": "00000000-0000-4000-8000-000000000001"},
        {"message": "Hi", "period": "year"},
    ],
)
async def test_validation_rejects_bad_input_and_control_fields(body):
    instance = app()
    response = await post(instance, body)
    assert response.status_code == 422
    instance.state.energy_chat.answer.assert_not_awaited()


async def test_authorized_chat_uses_server_report_and_context():
    instance = app()
    numerical, context = object(), {"occupancy": {"valid_sample_count": 0}}
    with patch(
        "app.api.v1.routers.analytics._report_and_context",
        new=AsyncMock(return_value=(numerical, context)),
    ) as loader:
        response = await post(
            instance,
            {
                "message": "  What does this mean?  ",
                "history": [{"role": "user", "content": "Hi"}],
                "date": "2026-10-25",
                "period": "week",
            },
        )
    assert response.status_code == 200
    assert response.json()["answer"] == "Hi."
    assert loader.await_args.args[3:] == (date(2026, 10, 25), "week")
    instance.state.energy_chat.answer.assert_awaited_once_with(
        numerical, context, "What does this mean?", [ChatTurn(role="user", content="Hi")]
    )


async def test_storage_failure_is_a_scoped_unavailable_answer():
    instance = app()
    with patch(
        "app.api.v1.routers.analytics._report_and_context",
        new=AsyncMock(side_effect=SQLAlchemyError("private db detail")),
    ):
        response = await post(instance, {"message": "Hi"})
    assert response.status_code == 200
    assert response.json()["status"] == "unavailable"
    assert "private db detail" not in response.text


@pytest.mark.parametrize("token", [None, "wrong-token"])
async def test_open_chat_needs_no_report_token(token):
    instance = app(analyst_chat_open=True)
    with patch(
        "app.api.v1.routers.analytics._report_and_context",
        new=AsyncMock(return_value=(object(), {})),
    ):
        response = await post(instance, {"message": "Hi"}, token=token)
    assert response.status_code == 200
    instance.state.energy_chat.answer.assert_awaited_once()


async def test_open_chat_works_without_any_server_token_configured():
    instance = app(token=None, analyst_chat_open=True)
    with patch(
        "app.api.v1.routers.analytics._report_and_context",
        new=AsyncMock(return_value=(object(), {})),
    ):
        response = await post(instance, {"message": "Hi"}, token=None)
    assert response.status_code == 200


async def test_open_chat_still_refuses_remote_clients_without_the_bridge():
    instance = app(analyst_chat_open=True)
    response = await post(instance, {"message": "Hi"}, token=None, host="203.0.113.2")
    assert response.status_code == 403
    instance.state.energy_chat.answer.assert_not_awaited()


async def test_open_chat_does_not_open_the_ai_report():
    instance = app(analyst_chat_open=True)
    instance.state.environmental_analyst = SimpleNamespace(generate=AsyncMock())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=instance, client=("127.0.0.1", 1234)),
        base_url="http://test",
    ) as client:
        response = await client.post(PATH.replace("/chat", "/report"), json={})
    assert response.status_code == 401
    instance.state.environmental_analyst.generate.assert_not_awaited()
