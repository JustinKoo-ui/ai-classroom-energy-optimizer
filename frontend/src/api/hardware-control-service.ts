import type {
  DashboardDevice,
  DeviceControlRequest,
} from "@/src/types/dashboard";
import type {
  LocalDeviceCommandStatus,
  LocalDeviceControlState,
  LocalDeviceControlStateList,
  LocalManualCommandResponse,
  LocalUrgencyStatus,
} from "@/src/types/device-control";
import type { UUID } from "@/src/types/contracts";

const TERMINAL_COMMAND_STATES = new Set(["applied", "rejected", "failed", "timed_out"]);

export type HardwareControlMode = "local" | "remote" | "demo";

export function getHardwareControlMode(): HardwareControlMode {
  if (typeof window === "undefined") return "demo";
  if (["localhost", "127.0.0.1", "::1", "[::1]"].includes(window.location.hostname)) {
    return process.env.NEXT_PUBLIC_ENABLE_HARDWARE_CONTROLS === "true" ? "local" : "demo";
  }
  const expectedOrigin = process.env.NEXT_PUBLIC_HARDWARE_CONTROL_ORIGIN;
  if (process.env.NEXT_PUBLIC_ENABLE_REMOTE_HARDWARE_CONTROLS === "true"
    && window.location.protocol === "https:"
    && expectedOrigin === window.location.origin
    && (process.env.NEXT_PUBLIC_API_BASE_URL ?? "") === "") {
    return "remote";
  }
  return "demo";
}

export function isLocalHardwareControlEnabled(): boolean {
  return getHardwareControlMode() === "local";
}

export function isHardwareDeviceStateFresh(state: LocalDeviceControlState | undefined, now = Date.now()): boolean {
  if (!state || state.operational_status !== "online" || state.freshness !== "fresh"
    || state.fault_codes.length > 0 || typeof state.reported_state.on !== "boolean"
    || !["auto", "manual"].includes(String(state.reported_state.mode))) return false;
  const age = now - Date.parse(state.reported_at ?? "");
  return Number.isFinite(age) && age >= -5000 && age <= 15_000;
}

export function withHardwareState(
  device: DashboardDevice,
  hardwareDevices: LocalDeviceControlState[] | undefined,
  error: string | null,
  pendingDeviceIds: ReadonlySet<string>,
  now = Date.now(),
): DashboardDevice {
  const reported = hardwareDevices?.find((candidate) => candidate.type === device.kind);
  const isOn = reported?.reported_state.on;
  const mode = reported?.reported_state.mode;
  const connected = !error && isHardwareDeviceStateFresh(reported, now);
  const pending = pendingDeviceIds.has(reported?.device_id ?? device.id)
    || (reported?.pending_command_id !== null && reported?.pending_command_id !== undefined);

  let statusLabel = "Reported by ESP32";
  let lockReason: string | undefined;
  if (error) {
    statusLabel = "Controls unavailable";
    lockReason = "Controls are locked until hardware access is restored. Check the message above.";
  } else if (!reported) {
    statusLabel = "Controller state unavailable";
    lockReason = "Controls are locked until controller state is available.";
  } else if (reported.operational_status === "offline") {
    statusLabel = "Controller offline";
    lockReason = "Controls are locked until the controller reconnects.";
  } else if (reported.fault_codes.length > 0) {
    statusLabel = "Device fault";
    lockReason = "Controls are locked while the controller reports a fault.";
  } else if (!connected) {
    statusLabel = "Reported state unavailable or stale";
    lockReason = "Controls are locked until fresh controller state is received.";
  } else if (pending) {
    statusLabel = "Awaiting ESP32 acknowledgement";
  }

  return {
    ...device,
    id: reported?.device_id ?? device.id,
    is_on: typeof isOn === "boolean" ? isOn : false,
    mode: mode === "manual" ? "manual" : "auto",
    connected,
    pending,
    active_power_w: 0,
    status_label: statusLabel,
    control_lock_reason: lockReason,
    hardware_backed: true,
  };
}

export class HardwareControlService {
  private readonly apiBaseUrl: string;
  private readonly classroomId: UUID;
  private readonly mode: HardwareControlMode;

  constructor(classroomId: UUID) {
    this.classroomId = classroomId;
    this.mode = getHardwareControlMode();
    this.apiBaseUrl = this.mode === "remote" ? "" : process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";
  }

  private requireEnabled() {
    if (this.mode === "demo") throw new Error("Hardware controls are not enabled for this website.");
  }

  async getDeviceStates(): Promise<LocalDeviceControlStateList> {
    this.requireEnabled();
    const response = await fetch(
      `${this.apiBaseUrl}/api/v1/classrooms/${this.classroomId}/devices`,
      { cache: "no-store", credentials: "include", signal: AbortSignal.timeout(15_000) },
    );
    if (!response.ok) throw new Error(await responseError(response, "Could not load ESP32 device state"));
    return (await response.json()) as LocalDeviceControlStateList;
  }

  async getUrgencyStatus(): Promise<LocalUrgencyStatus> {
    this.requireEnabled();
    const response = await fetch(
      `${this.apiBaseUrl}/api/v1/classrooms/${this.classroomId}/urgency`,
      { cache: "no-store", credentials: "include", signal: AbortSignal.timeout(15_000) },
    );
    if (!response.ok) {
      throw new Error(await responseError(response, "Could not load urgency status"));
    }
    return (await response.json()) as LocalUrgencyStatus;
  }

  async activateUrgency(): Promise<LocalUrgencyStatus> {
    this.requireEnabled();
    if (this.mode !== "local") throw new Error("Urgency activation is available from localhost only.");
    const response = await fetch(
      `${this.apiBaseUrl}/api/v1/classrooms/${this.classroomId}/urgency`,
      {
        method: "POST",
        cache: "no-store",
        credentials: "include",
      },
    );
    if (!response.ok) {
      const body = (await response.json().catch(() => null)) as
        | { detail?: unknown }
        | null;
      throw new Error(
        typeof body?.detail === "string"
          ? body.detail
          : `Urgency request was not accepted (${response.status}).`,
      );
    }
    return (await response.json()) as LocalUrgencyStatus;
  }

  async requestControl(request: DeviceControlRequest): Promise<void> {
    this.requireEnabled();
    const command = toLocalCommand(request);
    const response = await fetch(
      `${this.apiBaseUrl}/api/v1/devices/${request.device_id}/commands`,
      {
        method: "POST",
        cache: "no-store",
        credentials: "include",
        signal: AbortSignal.timeout(15_000),
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
      throw new Error(await responseError(response, "Control request was not accepted"));
    }
    const decision = (await response.json()) as LocalManualCommandResponse;
    if (decision.decision === "denied" || decision.command_id === null) {
      throw new Error(explainDenial(decision.reason_codes));
    }

    const finalStatus = await this.waitForCommand(decision.command_id, request.device_id);
    if (finalStatus.status !== "applied") {
      throw new Error(explainCommandFailure(finalStatus));
    }
  }

  private async waitForCommand(commandId: UUID, deviceId: UUID): Promise<LocalDeviceCommandStatus> {
    const deadline = Date.now() + 30_000;
    while (Date.now() < deadline) {
      const response = await fetch(
        `${this.apiBaseUrl}/api/v1/commands/${commandId}`,
        { cache: "no-store", credentials: "include", signal: AbortSignal.timeout(15_000) },
      );
      if (!response.ok) {
        throw new Error(await responseError(response, "Could not confirm ESP32 command"));
      }
      const command = (await response.json()) as LocalDeviceCommandStatus;
      if (command.command_id !== commandId || command.device_id !== deviceId) {
        throw new Error("The command acknowledgement did not match this device request.");
      }
      if (TERMINAL_COMMAND_STATES.has(command.status)) return command;
      await new Promise((resolve) => setTimeout(resolve, 500));
    }
    throw new Error(
      "ESP32 acknowledgement timed out; the dashboard keeps the last confirmed device state.",
    );
  }
}

function toLocalCommand(
  request: DeviceControlRequest,
): { action: "set_power" | "set_mode"; value: { on: boolean } | { mode: string } } {
  if (Object.keys(request.patch).length !== 1) {
    throw new Error("Send one relay ON/OFF or Auto/Manual change at a time.");
  }
  if (request.patch.mode === "auto" || request.patch.mode === "manual") {
    return { action: "set_mode", value: { mode: request.patch.mode } };
  }
  if (typeof request.patch.is_on === "boolean") {
    return { action: "set_power", value: { on: request.patch.is_on } };
  }
  throw new Error(
    "This relay prototype supports ON/OFF and Auto/Manual only; variable controls are simulated.",
  );
}

function explainDenial(reasonCodes: string[]): string {
  if (reasonCodes.includes("urgency_active")) {
    return "Manual controls are paused while urgency monitoring is active.";
  }
  if (reasonCodes.includes("device_offline") || reasonCodes.includes("device_state_stale")) {
    return "The ESP32 is offline or its reported state is stale. The request was denied.";
  }
  if (reasonCodes.includes("device_fault")) {
    return "The device reports a fault. The request was denied.";
  }
  return `The control policy denied this request (${reasonCodes.join(", ")}).`;
}

async function responseError(response: Response, fallback: string): Promise<string> {
  const body = (await response.json().catch(() => null)) as { detail?: unknown } | null;
  if (response.status === 401) return "Sign in through Cloudflare Access again before using remote controls.";
  if (response.status === 403) {
    const details: Record<string, string> = {
      "Your Cloudflare account is not authorized for relay controls.":
        "Your Cloudflare account is not authorized for relay controls. Sign in with an allowed operator account.",
      "Private monitoring bridge access denied.":
        "The private hardware connection is not authorized. Check the backend remote control and operator settings.",
      "This website origin is not allowed.":
        "This website is not authorized for hardware controls. Check the configured website origin.",
      "This request origin is not allowed.":
        "This request is not authorized for hardware controls. Reload the configured website and try again.",
      "Device control is localhost-only.":
        "This backend only allows local hardware controls. Check the authenticated remote control configuration.",
    };
    return typeof body?.detail === "string" && Object.hasOwn(details, body.detail)
      ? details[body.detail]
      : "This hardware control request is not authorized. Check the account and remote control configuration.";
  }
  return typeof body?.detail === "string" ? body.detail : `${fallback} (${response.status}).`;
}

function explainCommandFailure(command: LocalDeviceCommandStatus): string {
  if (command.status === "timed_out") {
    return "The command expired before the ESP32 confirmed it. The last confirmed state is unchanged.";
  }
  return `The ESP32 did not apply the command (${command.reason_code ?? command.status}).`;
}
