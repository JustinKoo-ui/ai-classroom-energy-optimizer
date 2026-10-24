"""Local prototype contracts for policy-evaluated relay commands."""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import Field, model_validator

from .common import CommandAction, CommandStatus, ContractModel, JsonObject, UtcDateTime


class ManualCommandRequest(ContractModel):
    action: Literal[CommandAction.SET_POWER, CommandAction.SET_MODE]
    value: JsonObject
    reason: str = Field(min_length=1, max_length=500)
    expires_in_seconds: int = Field(default=30, ge=1, le=30)

    @model_validator(mode="after")
    def validate_action_value(self) -> ManualCommandRequest:
        if self.action is CommandAction.SET_POWER:
            if set(self.value) != {"on"} or not isinstance(self.value["on"], bool):
                raise ValueError("set_power requires exactly one boolean 'on' value")
        elif set(self.value) != {"mode"} or self.value["mode"] not in {"auto", "manual"}:
            raise ValueError("set_mode requires mode 'auto' or 'manual'")
        return self


class ManualCommandResponse(ContractModel):
    request_id: UUID
    decision_id: UUID
    decision: Literal["allowed", "denied"]
    reason_codes: list[str]
    summary: str
    command_id: UUID | None
    command_status: CommandStatus | None


class DeviceControlState(ContractModel):
    device_id: UUID
    name: str
    type: str
    operational_status: str
    reported_state: JsonObject
    fault_codes: list[str]
    state_version: int
    last_seen_at: UtcDateTime | None
    reported_at: UtcDateTime | None
    freshness: Literal["fresh", "stale", "unknown"]
    pending_command_id: UUID | None
    pending_command_status: CommandStatus | None


class DeviceControlStateList(ContractModel):
    classroom_id: UUID
    devices: list[DeviceControlState]


class DeviceCommandStatus(ContractModel):
    command_id: UUID
    device_id: UUID
    decision_id: UUID
    status: CommandStatus
    reason_code: str | None
    reported_state: JsonObject | None
    created_at: UtcDateTime
    updated_at: UtcDateTime
