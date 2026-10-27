"""Remote control authorization and audit tests; no live devices or broker."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI

from app.api.v1.routers.analytics import router as analytics_router
from app.api.v1.routers.device_control import router as hardware_router
from app.core.config import Settings
from app.core.private_bridge import (
    OPERATOR_EMAIL_HEADER,
    PROXY_TOKEN_HEADER,
    PrivateBridgeMiddleware,
)
from app.domain.local_hardware import (
    LIGHT_DEVICE_ID,
    LOCAL_CLASSROOM_ID,
    LOCAL_RELAY_DEVICE_IDS,
    LOCAL_SITE_ID,
)
from app.schemas.device_control import ManualCommandRequest, ManualCommandResponse
from app.services.device_control import (
    DeviceCommandSubmission,
    IdempotencyConflictError,
    _existing_idempotent_request,
    submit_manual_command,
)

TOKEN = "test-remote-control-server-secret-at-least-32-characters"
OPERATOR = "operator@example.com"
BASE = f"/api/v1/classrooms/{LOCAL_CLASSROOM_ID}"
COMMAND_PATH = f"/api/v1/devices/{LIGHT_DEVICE_ID}/commands"


def app(**overrides):
    settings = Settings(_env_file=None, **{
        "database_url": "postgresql+asyncpg://unused",
        "classroom_api_proxy_enabled": True,
        "classroom_api_proxy_token": TOKEN,
        "classroom_remote_control_enabled": True,
        "classroom_remote_control_allowed_emails": OPERATOR,
        **overrides,
    })
    instance = FastAPI()
    instance.add_middleware(PrivateBridgeMiddleware)
    instance.include_router(hardware_router)
    instance.include_router(analytics_router)
    instance.state.analytics_settings = settings
    instance.state.local_hardware_control_enabled = True
    instance.state.sessions = MagicMock()
    instance.state.command_dispatcher = MagicMock()
    return instance


def headers(**overrides):
    return {
        PROXY_TOKEN_HEADER: TOKEN,
        OPERATOR_EMAIL_HEADER: OPERATOR,
        "Idempotency-Key": str(uuid4()),
        **overrides,
    }


async def send(instance, path, *, method="GET", host="203.0.113.10", provided=None, body=None):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=instance, client=(host, 1234)),
        base_url="http://test",
    ) as client:
        return await client.request(method, path, headers=provided, json=body)


def power_body(on=True):
    return {"action": "set_power", "value": {"on": on}, "reason": "Remote dashboard"}


def database(instance, *, rows=None, row=None):
    session = MagicMock()
    result = MagicMock()
    result.mappings.return_value = result
    result.all.return_value = rows or []
    result.one_or_none.return_value = row
    session.execute = AsyncMock(return_value=result)
    instance.state.sessions.return_value.__aenter__ = AsyncMock(return_value=session)
    instance.state.sessions.return_value.__aexit__ = AsyncMock(return_value=None)
    return session


@pytest.mark.parametrize("host", ["127.0.0.1", "203.0.113.10"])
@pytest.mark.parametrize("overrides", [
    {"classroom_remote_control_enabled": False},
    {"classroom_remote_control_allowed_emails": ""},
    {"classroom_remote_control_allowed_emails": "someone-else@example.com"},
    {"classroom_api_proxy_enabled": False},
    {"classroom_api_proxy_token": None},
    {"classroom_api_proxy_classroom_id": uuid4()},
])
async def test_control_is_denied_before_database_when_configuration_disallows_it(host, overrides):
    instance = app(**overrides)
    response = await send(instance, COMMAND_PATH, method="POST", host=host,
                          provided=headers(), body=power_body())
    assert response.status_code == 403
    instance.state.sessions.assert_not_called()
    instance.state.command_dispatcher.wake.assert_not_called()


@pytest.mark.parametrize("host", ["127.0.0.1", "203.0.113.10"])
@pytest.mark.parametrize("provided", [
    {},
    {PROXY_TOKEN_HEADER: TOKEN},
    {OPERATOR_EMAIL_HEADER: OPERATOR},
    {PROXY_TOKEN_HEADER: "wrong", OPERATOR_EMAIL_HEADER: OPERATOR},
    {PROXY_TOKEN_HEADER: "", OPERATOR_EMAIL_HEADER: OPERATOR},
    {PROXY_TOKEN_HEADER: TOKEN, OPERATOR_EMAIL_HEADER: ""},
    {PROXY_TOKEN_HEADER: TOKEN, OPERATOR_EMAIL_HEADER: "intruder@example.com"},
    {PROXY_TOKEN_HEADER: TOKEN, OPERATOR_EMAIL_HEADER: f"{OPERATOR},intruder@example.com"},
])
async def test_missing_or_invalid_credentials_do_not_become_local_authority(host, provided):
    # A completely header-free local call intentionally retains its old behavior.
    if host == "127.0.0.1" and not provided:
        return
    instance = app()
    response = await send(instance, COMMAND_PATH, method="POST", host=host,
                          provided={"Idempotency-Key": "test", **provided}, body=power_body())
    assert response.status_code == 403
    assert TOKEN not in response.text
    instance.state.sessions.assert_not_called()


@pytest.mark.parametrize("duplicate", [PROXY_TOKEN_HEADER, OPERATOR_EMAIL_HEADER])
async def test_duplicate_authentication_headers_are_rejected(duplicate):
    provided = list(headers().items())
    provided.append((duplicate, TOKEN if duplicate == PROXY_TOKEN_HEADER else OPERATOR))
    instance = app()
    response = await send(instance, f"{BASE}/devices", provided=provided)
    assert response.status_code == 403
    instance.state.sessions.assert_not_called()


@pytest.mark.parametrize(("method", "path"), [
    ("POST", f"{BASE}/urgency"),
    ("POST", f"/api/v1/devices/{uuid4()}/commands"),
    ("GET", f"/api/v1/classrooms/{uuid4()}/devices"),
    ("GET", f"/api/v1/classrooms/{uuid4()}/urgency"),
    ("POST", f"{BASE}/devices"),
    ("DELETE", COMMAND_PATH),
    ("GET", COMMAND_PATH),
    ("GET", "/api/v1/commands/not-a-uuid"),
    ("GET", f"/api/v1/commands/{uuid4()}/"),
    ("POST", f"/api/v1/commands/{uuid4()}"),
    ("GET", "/openapi.json"),
    ("GET", "/health/live"),
])
async def test_other_paths_classrooms_devices_and_urgency_activation_remain_denied(method, path):
    instance = app()
    response = await send(instance, path, method=method, provided=headers(), body={})
    assert response.status_code == 403
    instance.state.sessions.assert_not_called()
    instance.state.command_dispatcher.wake.assert_not_called()


async def test_authorized_remote_device_state_read_keeps_database_scope():
    instance = app()
    session = database(instance)
    response = await send(instance, f"{BASE}/devices", provided=headers())
    assert response.status_code == 200
    assert response.json()["devices"] == []
    query, params = session.execute.await_args.args
    assert params["classroom_id"] == LOCAL_CLASSROOM_ID
    assert params["site_id"] == LOCAL_SITE_ID
    assert set(params["device_ids"]) == LOCAL_RELAY_DEVICE_IDS
    assert "d.classroom_id = :classroom_id" in str(query)


async def test_authorized_remote_command_status_is_scoped_to_seeded_devices_and_classroom():
    instance = app()
    session = database(instance)
    command_id = uuid4()
    response = await send(instance, f"/api/v1/commands/{command_id}", provided=headers())
    assert response.status_code == 404  # Valid auth; unrelated/nonexistent commands stay hidden.
    query, params = session.execute.await_args.args
    assert params["command_id"] == command_id
    assert params["classroom_id"] == LOCAL_CLASSROOM_ID
    assert params["site_id"] == LOCAL_SITE_ID
    assert set(params["device_ids"]) == LOCAL_RELAY_DEVICE_IDS
    assert "d.classroom_id = :classroom_id" in str(query)
    assert "d.id = ANY" in str(query)


async def test_remote_operator_can_read_urgency_status_without_activating_it(monkeypatch):
    instance = app()
    reader = AsyncMock(return_value={"status": "inactive", "started_at": None, "expires_at": None})
    monkeypatch.setattr("app.api.v1.routers.device_control.get_local_urgency_status", reader)
    response = await send(instance, f"{BASE}/urgency", provided=headers())
    assert response.status_code == 200
    reader.assert_awaited_once()
    instance.state.command_dispatcher.wake.assert_not_called()


@pytest.mark.parametrize("value", [{"on": True}, {"on": False}, {"mode": "auto"},
                                   {"mode": "manual"}])
async def test_authorized_relay_commands_use_existing_policy_submission_with_operator_audit(
    monkeypatch, value,
):
    instance = app(classroom_remote_control_allowed_emails=f"other@example.com, {OPERATOR}")
    submit = AsyncMock(return_value=DeviceCommandSubmission(
        ManualCommandResponse(request_id=uuid4(), decision_id=uuid4(), decision="allowed",
                              reason_codes=["authorized_manual_request"], summary="Test policy",
                              command_id=uuid4(), command_status="queued"),
        replayed=False,
    ))
    monkeypatch.setattr("app.api.v1.routers.device_control.submit_manual_command", submit)
    response = await send(instance, COMMAND_PATH, method="POST", provided=headers(**{
        OPERATOR_EMAIL_HEADER: OPERATOR.upper(),
    }), body={"action": "set_power" if "on" in value else "set_mode",
              "value": value, "reason": "Remote dashboard"})
    assert response.status_code == 200
    assert response.json()["command_status"] == "queued"
    assert submit.await_args.kwargs["operator_email"] == OPERATOR
    assert submit.await_args.kwargs["device_id"] == LIGHT_DEVICE_ID
    assert submit.await_args.kwargs["request"].value == value
    instance.state.command_dispatcher.wake.assert_called_once()


@pytest.mark.parametrize("value", [{"on": True, "brightness_pct": 50}, {"fan_speed": 2},
                                   {"target_temperature_c": 26}, {"mode": "scheduled"}])
async def test_remote_variable_controls_remain_contract_rejected(value):
    instance = app()
    response = await send(instance, COMMAND_PATH, method="POST", provided=headers(),
                          body={"action": "set_power" if "on" in value else "set_mode",
                                "value": value, "reason": "Unsupported control"})
    assert response.status_code == 422
    instance.state.sessions.assert_not_called()
    instance.state.command_dispatcher.wake.assert_not_called()


async def test_disabled_local_hardware_flag_still_prevents_remote_control():
    instance = app()
    instance.state.local_hardware_control_enabled = False
    response = await send(instance, f"{BASE}/devices", provided=headers())
    assert response.status_code == 404
    instance.state.sessions.assert_not_called()


async def test_local_direct_control_retains_existing_access_and_no_remote_attribution(monkeypatch):
    instance = app(classroom_remote_control_enabled=False)
    submit = AsyncMock(return_value=DeviceCommandSubmission(
        ManualCommandResponse(request_id=uuid4(), decision_id=uuid4(), decision="denied",
                              reason_codes=["device_offline"], summary="Offline",
                              command_id=None, command_status=None),
        replayed=False,
    ))
    monkeypatch.setattr("app.api.v1.routers.device_control.submit_manual_command", submit)
    response = await send(instance, COMMAND_PATH, method="POST", host="127.0.0.1",
                          provided={"Idempotency-Key": "local"}, body=power_body())
    assert response.status_code == 200
    assert response.json()["decision"] == "denied"
    assert submit.await_args.kwargs["operator_email"] is None
    instance.state.command_dispatcher.wake.assert_not_called()


async def test_read_only_bridge_needs_no_operator_even_when_remote_control_enabled(monkeypatch):
    instance = app()
    reader = AsyncMock(return_value=None)
    monkeypatch.setattr("app.api.v1.routers.analytics.get_analytics_report", reader)
    response = await send(instance, f"{BASE}/analytics", provided={PROXY_TOKEN_HEADER: TOKEN})
    assert response.status_code == 404  # Existing analytics route ran, with no mocked classroom.
    reader.assert_awaited_once()


def audit_sessions(*, online=True):
    now = datetime.now(UTC)
    device = SimpleNamespace(
        classroom_id=LOCAL_CLASSROOM_ID, control_policy_id=uuid4(), enabled=True,
        capabilities={"supported_actions": ["set_power", "set_mode"]},
        operational_status="online" if online else "offline",
        reported_state={"mode": "manual", "on": False}, fault_codes=[], last_seen_at=now,
        environment_at=now, motion_detected=True, sample_interval_ms=2000,
        urgency_active=False, pending_command_id=None, previous_applied_at=None,
    )
    session = MagicMock()

    async def execute(query, params=None):
        result = MagicMock()
        result.mappings.return_value = result
        sql = str(query)
        result.one_or_none.return_value = (
            device if "FOR UPDATE OF d" in sql else None
        )
        return result

    session.execute = AsyncMock(side_effect=execute)
    session.begin.return_value.__aenter__ = AsyncMock(return_value=session)
    session.begin.return_value.__aexit__ = AsyncMock(return_value=None)
    sessions = MagicMock()
    sessions.return_value.__aenter__ = AsyncMock(return_value=session)
    sessions.return_value.__aexit__ = AsyncMock(return_value=None)
    return sessions, session


@pytest.mark.parametrize("online", [True, False])
async def test_remote_identity_is_persisted_with_policy_decision_and_only_allowed_outbox(online):
    sessions, session = audit_sessions(online=online)
    submission = await submit_manual_command(
        sessions, device_id=LIGHT_DEVICE_ID, idempotency_key="remote-audit-test",
        request=ManualCommandRequest(**power_body()), operator_email=OPERATOR,
    )
    calls = [(str(call.args[0]), call.args[1]) for call in session.execute.await_args_list]
    decision = next(params for sql, params in calls if "INSERT INTO control_decisions" in sql)
    import json
    snapshot = json.loads(decision["input_snapshot"])
    assert snapshot["remote_operator_email"] == OPERATOR
    assert snapshot["authentication_source"] == "cloudflare_access"
    assert submission.response.decision == ("allowed" if online else "denied")
    assert sum("INSERT INTO device_commands" in sql for sql, _ in calls) == int(online)
    if not online:
        assert "device_offline" in submission.response.reason_codes


@pytest.mark.parametrize("previous_operator", [None, "other@example.com", OPERATOR])
async def test_idempotent_replay_preserves_operator_attribution(previous_operator):
    import json
    now = datetime.now(UTC)
    result = MagicMock()
    result.mappings.return_value = result
    result.one_or_none.return_value = SimpleNamespace(
        request_id=uuid4(), decision_id=uuid4(), device_id=LIGHT_DEVICE_ID,
        requested_action="set_power", requested_value={"on": True, "mode": "manual",
                                                       "source": "manual"},
        reason="Remote dashboard", requested_at=now, expires_at=now + timedelta(seconds=30),
        outcome="allowed", reason_codes=["authorized_manual_request"], summary="Test",
        command_id=uuid4(), command_status="applied",
        input_snapshot=json.dumps({"remote_operator_email": previous_operator}),
    )
    session = SimpleNamespace(execute=AsyncMock(return_value=result))
    operation = _existing_idempotent_request(
        session, idempotency_key="same-key", device_id=LIGHT_DEVICE_ID,
        request=ManualCommandRequest(**power_body()), operator_email=OPERATOR,
    )
    if previous_operator != OPERATOR:
        with pytest.raises(IdempotencyConflictError):
            await operation
    else:
        response = await operation
        assert response.command_status.value == "applied"


def test_remote_control_defaults_to_disabled_with_no_authorized_operators():
    settings = Settings(_env_file=None, database_url="postgresql+asyncpg://unused")
    assert not settings.classroom_remote_control_enabled
    assert settings.classroom_remote_control_allowed_emails == ""
