"use client";

import { useEffect, useState } from "react";
import type { DashboardDevice, DeviceControlRequest } from "@/src/types/dashboard";

interface DeviceControlCardProps {
  device: DashboardDevice;
  onRequest: (request: DeviceControlRequest) => Promise<void>;
  compact?: boolean;
}

export function DeviceControlCard({
  device,
  onRequest,
  compact = false,
}: DeviceControlCardProps) {
  const disabled = !device.connected || device.pending;
  const powerControlDisabled =
    disabled || (device.hardware_backed && device.mode === "auto");
  const manualControlDisabled = disabled || device.mode === "auto";
  const submit = (patch: DeviceControlRequest["patch"], reason: string) =>
    onRequest({ device_id: device.id, patch, reason });

  return (
    <article className={`device-card${compact ? " device-card--compact" : ""}`}>
      <header className="device-card__header">
        <div>
          <span className="eyebrow">{device.location}</span>
          <h3>{device.name}</h3>
        </div>
        <button
          className={`power-toggle ${device.is_on ? "power-toggle--on" : ""}`}
          type="button"
          role="switch"
          aria-checked={device.is_on}
          aria-label={`Turn ${device.name} ${device.is_on ? "off" : "on"}`}
          disabled={powerControlDisabled}
          onClick={() => submit({ is_on: !device.is_on }, "Dashboard power control")}
        >
          <span />
        </button>
      </header>

      <div className="device-card__mode" aria-label={`${device.name} control mode`}>
        {(["auto", "manual"] as const).map((mode) => (
          <button
            key={mode}
            type="button"
            className={device.mode === mode ? "is-active" : ""}
            disabled={disabled}
            onClick={() => submit({ mode }, "Dashboard mode control")}
          >
            {mode}
          </button>
        ))}
      </div>
      {device.hardware_backed && device.mode === "auto" && (
        <small>Switch to Manual before using the relay power control.</small>
      )}

      {device.kind === "air_conditioner" && device.target_temperature_c !== null && (
        <div className="temperature-control">
          <button
            type="button"
            aria-label="Decrease target temperature"
            disabled={disabled || device.hardware_backed || device.target_temperature_c <= 18}
            onClick={() =>
              submit(
                { target_temperature_c: device.target_temperature_c! - 1 },
                "Dashboard temperature adjustment",
              )
            }
          >
            −
          </button>
          <div>
            <strong>{device.target_temperature_c}°</strong>
            <span>Target temperature</span>
          </div>
          <button
            type="button"
            aria-label="Increase target temperature"
            disabled={disabled || device.hardware_backed || device.target_temperature_c >= 30}
            onClick={() =>
              submit(
                { target_temperature_c: device.target_temperature_c! + 1 },
                "Dashboard temperature adjustment",
              )
            }
          >
            +
          </button>
        </div>
      )}
      {device.kind === "air_conditioner" && device.target_temperature_c !== null && device.hardware_backed && (
        <small>Setpoint is simulated; this relay supports ON/OFF only.</small>
      )}

      {device.kind === "fan" && device.fan_speed !== null && (
        <div className="stepped-control">
          <div className="control-label-row">
            <span>Fan speed</span>
            <strong>{device.mode === "auto" ? `Auto · ${device.fan_speed}` : `Level ${device.fan_speed}`}</strong>
          </div>
          <div className="speed-steps" aria-label="Fan speed">
            {([0, 1, 2, 3] as const).map((speed) => (
              <button
                key={speed}
                type="button"
                className={device.fan_speed === speed ? "is-active" : ""}
                aria-label={`Set fan speed to ${speed}`}
                disabled={manualControlDisabled || device.hardware_backed}
                onClick={() =>
                  submit(
                    { fan_speed: speed },
                    "Dashboard fan speed adjustment",
                  )
                }
              >
                {speed}
              </button>
            ))}
          </div>
          {device.hardware_backed
            ? <small>Fan speed is simulated; this relay supports ON/OFF only.</small>
            : device.mode === "auto" && <small>Switch to Manual to choose a fixed speed.</small>}
        </div>
      )}

      {device.kind === "lighting" && device.brightness_pct !== null && (
        <BrightnessControl
          value={device.brightness_pct}
          disabled={manualControlDisabled || device.hardware_backed}
          automatic={device.mode === "auto"}
          onCommit={(brightness_pct) =>
            submit({ brightness_pct }, "Dashboard lighting brightness adjustment")
          }
        />
      )}
      {device.kind === "lighting" && device.brightness_pct !== null && device.hardware_backed && (
        <small>Brightness is simulated; this relay supports ON/OFF only.</small>
      )}

      {!device.connected && (
        <div className="device-card__offline-notice" role="status">
          Controls are locked until the controller reconnects.
        </div>
      )}

      <footer className="device-card__footer">
        <span className={device.connected ? "device-online" : "device-offline"}>
          {device.connected ? (device.pending ? "Pending approval" : device.status_label) : "Disconnected"}
        </span>
        <strong>
          {device.hardware_backed
            ? "Power not measured"
            : device.active_power_w >= 1000
              ? `${(device.active_power_w / 1000).toFixed(2)} kW`
              : `${device.active_power_w} W`}
        </strong>
      </footer>
    </article>
  );
}

function BrightnessControl({
  value,
  disabled,
  automatic,
  onCommit,
}: {
  value: number;
  disabled: boolean;
  automatic: boolean;
  onCommit: (value: number) => void;
}) {
  const [draft, setDraft] = useState(value);
  useEffect(() => setDraft(value), [value]);
  return (
    <div className="range-control">
      <div className="control-label-row">
        <span>Brightness</span>
        <strong>{automatic ? `Auto · ${value}%` : `${draft}%`}</strong>
      </div>
      <input
        type="range"
        min="0"
        max="100"
        step="5"
        value={draft}
        disabled={disabled}
        aria-label="Lighting brightness percentage"
        style={{ "--range-progress": `${draft}%` } as React.CSSProperties}
        onChange={(event) => setDraft(Number(event.target.value))}
        onPointerUp={() => onCommit(draft)}
        onKeyUp={(event) => {
          if (["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) onCommit(draft);
        }}
      />
      {automatic && <small>Daylight compensation is controlling brightness.</small>}
    </div>
  );
}
