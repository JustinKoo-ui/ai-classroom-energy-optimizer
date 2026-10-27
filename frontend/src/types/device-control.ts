import type { CommandStatus, JsonObject, UUID } from "./contracts";

export interface LocalDeviceControlState {
  device_id: UUID;
  name: string;
  type: "fan" | "lighting" | "air_conditioner";
  operational_status: string;
  reported_state: JsonObject;
  fault_codes: string[];
  state_version: number;
  last_seen_at: string | null;
  reported_at: string | null;
  freshness: "fresh" | "stale" | "unknown";
  pending_command_id: UUID | null;
  pending_command_status: CommandStatus | null;
}

export interface LocalDeviceControlStateList {
  classroom_id: UUID;
  devices: LocalDeviceControlState[];
}

export interface LocalManualCommandResponse {
  request_id: UUID;
  decision_id: UUID;
  decision: "allowed" | "denied";
  reason_codes: string[];
  summary: string;
  command_id: UUID | null;
  command_status: CommandStatus | null;
}

export interface LocalDeviceCommandStatus {
  command_id: UUID;
  device_id: UUID;
  decision_id: UUID;
  status: CommandStatus;
  reason_code: string | null;
  reported_state: JsonObject | null;
  created_at: string;
  updated_at: string;
}

export interface LocalUrgencyStatus {
  status:
    | "inactive"
    | "monitoring"
    | "completed_no_presence"
    | "completed_presence"
    | "completed_camera_unknown";
  started_at: string | null;
  expires_at: string | null;
}
