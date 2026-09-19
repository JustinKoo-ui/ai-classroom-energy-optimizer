import type {
  DashboardData,
  DashboardRealtimeEvent,
  DashboardService,
  DeviceControlRequest,
} from "@/src/types/dashboard";
import type { UUID } from "@/src/types/contracts";

export class FastApiDashboardService implements DashboardService {
  constructor(
    private readonly apiBaseUrl: string,
    private readonly classroomId: UUID,
  ) {}

  async getInitialState(): Promise<DashboardData> {
    const response = await fetch(
      `${this.apiBaseUrl}/api/v1/classrooms/${this.classroomId}/dashboard`,
      { credentials: "include" },
    );
    if (!response.ok) throw new Error("Unable to load classroom dashboard");
    return (await response.json()) as DashboardData;
  }

  subscribe(listener: (event: DashboardRealtimeEvent) => void): () => void {
    const url = new URL(this.apiBaseUrl);
    url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
    url.pathname = `/api/v1/ws/classrooms/${this.classroomId}`;
    const socket = new WebSocket(url);
    socket.addEventListener("message", (message) => {
      listener(JSON.parse(message.data as string) as DashboardRealtimeEvent);
    });
    return () => socket.close(1000, "Dashboard unmounted");
  }

  async requestDeviceControl(
    request: DeviceControlRequest,
  ): Promise<{ request_id: UUID }> {
    const command = toApiCommand(request);
    const response = await fetch(
      `${this.apiBaseUrl}/api/v1/devices/${request.device_id}/commands`,
      {
        method: "POST",
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
    if (!response.ok) throw new Error("Control request was not accepted");
    return (await response.json()) as { request_id: UUID };
  }
}

function toApiCommand(request: DeviceControlRequest): { action: string; value: object } {
  if (request.patch.target_temperature_c !== undefined) {
    return { action: "set_temperature", value: { setpoint_c: request.patch.target_temperature_c } };
  }
  if (request.patch.fan_speed !== undefined) {
    return { action: "set_fan_speed", value: { speed: request.patch.fan_speed } };
  }
  if (request.patch.brightness_pct !== undefined) {
    return { action: "set_brightness", value: { brightness_pct: request.patch.brightness_pct } };
  }
  if (request.patch.mode !== undefined) {
    return { action: "set_mode", value: { mode: request.patch.mode } };
  }
  return { action: "set_power", value: { on: request.patch.is_on } };
}
