import type {
  DeviceControlRequest,
} from "@/src/types/dashboard";
import type {
  LocalDeviceCommandStatus,
  LocalDeviceControlStateList,
  LocalManualCommandResponse,
} from "@/src/types/device-control";
import type { UUID } from "@/src/types/contracts";

const TERMINAL_COMMAND_STATES = new Set(["applied", "rejected", "failed", "timed_out"]);

export function isLocalHardwareControlEnabled(): boolean {
  if (process.env.NEXT_PUBLIC_ENABLE_HARDWARE_CONTROLS !== "true") return false;
  if (typeof window === "undefined") return false;
  return ["localhost", "127.0.0.1", "::1"].includes(window.location.hostname);
}

export class HardwareControlService {
  private readonly apiBaseUrl: string;

  constructor(private readonly classroomId: UUID) {
    this.apiBaseUrl =
      process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";
  }

  async getDeviceStates(): Promise<LocalDeviceControlStateList> {
    const response = await fetch(
      `${this.apiBaseUrl}/api/v1/classrooms/${this.classroomId}/devices`,
      { cache: "no-store", credentials: "include" },
    );
    if (!response.ok) throw new Error(`Could not load device state (${response.status}).`);
    return (await response.json()) as LocalDeviceControlStateList;
  }

  async requestControl(request: DeviceControlRequest): Promise<void> {
    const command = toLocalCommand(request);
    const response = await fetch(
      `${this.apiBaseUrl}/api/v1/devices/${request.device_id}/commands`,
      {
        method: "POST",
        cache: "no-store",
        credentials: "include",
        headers: {
          "Content-Type": "application/json",
          "Idempotency-Key": crypto.randomUUID(),
        },
        body: JSON.stringify({
          action: command.action,
          value: command.value,
          reason: request.reason,
          expires_in_seconds: 30,
        }),
      },
    );
    if (!response.ok) {
      throw new Error(`Control request was not accepted (${response.status}).`);
    }
    const decision = (await response.json()) as LocalManualCommandResponse;
    if (decision.decision === "denied" || decision.command_id === null) {
      throw new Error(explainDenial(decision.reason_codes));
    }

    const finalStatus = await this.waitForCommand(decision.command_id);
    if (finalStatus.status !== "applied") {
      throw new Error(explainCommandFailure(finalStatus));
    }
  }

  private async waitForCommand(commandId: UUID): Promise<LocalDeviceCommandStatus> {
    const deadline = Date.now() + 30_000;
    while (Date.now() < deadline) {
      const response = await fetch(
        `${this.apiBaseUrl}/api/v1/commands/${commandId}`,
        { cache: "no-store", credentials: "include" },
      );
      if (!response.ok) {
        throw new Error(`Could not confirm ESP32 command (${response.status}).`);
      }
      const command = (await response.json()) as LocalDeviceCommandStatus;
      if (TERMINAL_COMMAND_STATES.has(command.status)) return command;
      await new Promise((resolve) => window.setTimeout(resolve, 500));
    }
    throw new Error(
      "ESP32 acknowledgement timed out; the dashboard keeps the last confirmed device state.",
    );
  }
}

function toLocalCommand(
  request: DeviceControlRequest,
): { action: "set_power" | "set_mode"; value: { on: boolean } | { mode: string } } {
  if (request.patch.mode !== undefined) {
    return { action: "set_mode", value: { mode: request.patch.mode } };
  }
  if (request.patch.is_on !== undefined) {
    return { action: "set_power", value: { on: request.patch.is_on } };
  }
  throw new Error(
    "This relay prototype supports ON/OFF and Auto/Manual only; variable controls are simulated.",
  );
}

function explainDenial(reasonCodes: string[]): string {
  if (reasonCodes.includes("pir_master_no_motion")) {
    return "PIR master control is overriding the ON request because no motion is detected.";
  }
  if (reasonCodes.includes("safety_state_stale") || reasonCodes.includes("safety_state_unknown")) {
    return "The motion safety reading is missing or stale. The request was denied.";
  }
  if (reasonCodes.includes("device_offline") || reasonCodes.includes("device_state_stale")) {
    return "The ESP32 is offline or its reported state is stale. The request was denied.";
  }
  if (reasonCodes.includes("device_fault")) {
    return "The device reports a fault. The request was denied.";
  }
  return `The local control policy denied this request (${reasonCodes.join(", ")}).`;
}

function explainCommandFailure(command: LocalDeviceCommandStatus): string {
  if (command.reason_code === "pir_master_no_motion") {
    return "PIR master control overrode the ON request; the ESP32 reports the output OFF.";
  }
  if (command.status === "timed_out") {
    return "The command expired before the ESP32 confirmed it. The last confirmed state is unchanged.";
  }
  return `The ESP32 did not apply the command (${command.reason_code ?? command.status}).`;
}
