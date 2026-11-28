"use client";

import { useEffect, useRef, useState } from "react";
import type { TwinScene, TwinView } from "@/src/lib/solar-twin-scene";
import { compassPoint, twinState, withinTravel, type TwinState } from "@/src/lib/solar-twin";
import type { PrototypeMonitorSnapshot } from "@/src/types/prototype-monitor";

type Props = { snapshot: PrototypeMonitorSnapshot | null; error: string | null };

const VIEWS: Array<[TwinView, string]> = [["perspective", "3D"], ["top", "Top"], ["front", "Front"]];

function bearing(value: number | null) {
  return value === null ? "Unknown" : `${value.toFixed(1)}° ${compassPoint(value)}`;
}

function degrees(value: number | null, suffix = "") {
  return value === null ? "Unknown" : `${value.toFixed(1)}°${suffix}`;
}

function drive(value: boolean | null) {
  if (value === null) return "Unknown";
  return value ? "Drive requested" : "Stopped";
}

function summary(state: TwinState) {
  if (state.data === "none") return "No tracker data yet. The model is shown in a neutral pose facing south.";
  const pose = `Panel facing ${bearing(state.azimuthDeg)}, tilted ${degrees(state.tiltDeg)}`;
  const target = state.target
    ? `${state.target.kind === "sun" ? "Sun" : "Manual target"} at ${bearing(state.target.azimuthDeg)}, zenith ${degrees(state.target.zenithDeg)}`
    : "No target";
  const error = state.pointingErrorDeg === null ? "" : `, pointing error ${degrees(state.pointingErrorDeg)}`;
  return `${state.data === "live" ? "Live" : "Last known"}: ${pose}. ${target}${error}.`;
}

/** Digital twin of the solar tracker: a 3D model posed from live telemetry. */
export function SolarDigitalTwin({ snapshot, error }: Props) {
  const state = twinState(snapshot, error);
  const containerRef = useRef<HTMLDivElement>(null);
  const sceneRef = useRef<TwinScene | null>(null);
  const stateRef = useRef(state);
  const [renderer, setRenderer] = useState<"loading" | "ready" | "unsupported">("loading");
  const [view, setView] = useState<TwinView>("perspective");
  const stateKey = JSON.stringify(state);

  useEffect(() => {
    stateRef.current = JSON.parse(stateKey) as TwinState;
    sceneRef.current?.update(stateRef.current);
  }, [stateKey]);

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;
    let cancelled = false;
    // three.js is only downloaded when this page is opened.
    import("@/src/lib/solar-twin-scene").then(({ createTwinScene }) => {
      if (cancelled) return;
      try {
        const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
        const twin = createTwinScene(container, { reducedMotion });
        twin.update(stateRef.current);
        sceneRef.current = twin;
        setRenderer("ready");
      } catch {
        setRenderer("unsupported");
      }
    }, () => {
      if (!cancelled) setRenderer("unsupported");
    });
    return () => {
      cancelled = true;
      sceneRef.current?.dispose();
      sceneRef.current = null;
    };
  }, []);

  function chooseView(next: TwinView) {
    setView(next);
    sceneRef.current?.setView(next);
  }

  const target = state.target;
  const dataLabel = state.data === "live" ? "Live" : state.data === "stale" ? "Last known pose · data is stale" : "Waiting for tracker data";
  const outsideTravel = state.travelLimits && target ? !withinTravel(target.azimuthDeg, state.travelLimits) : false;

  return (
    <section className="solar-twin" aria-labelledby="solar-twin-title">
      <div className="section-heading">
        <div>
          <p className="eyebrow">Mirrors the tracker&apos;s reported angles</p>
          <h2 id="solar-twin-title">Digital twin</h2>
        </div>
        <span className={`solar-twin__badge solar-twin__badge--${state.data}`}>{dataLabel}</span>
      </div>

      <div className="solar-twin__layout">
        <div className="solar-twin__stage">
          <div className="solar-twin__canvas" ref={containerRef} role="img" aria-label={summary(state)} />
          {renderer === "loading" && <p className="solar-twin__overlay" role="status">Loading 3D model…</p>}
          {renderer === "unsupported" && (
            <p className="solar-twin__overlay" role="status">
              3D view unavailable in this browser (WebGL is off). The readings beside it are still live.
            </p>
          )}
          {renderer === "ready" && state.data !== "live" && (
            <p className="solar-twin__note" role="status">
              {state.data === "stale"
                ? "Showing the last reported pose. Motors and power are not shown until fresh data arrives."
                : "No tracker data yet: neutral pose facing south. Nothing on this model is live."}
            </p>
          )}
          <div className="solar-twin__views" role="group" aria-label="Camera view">
            {VIEWS.map(([id, label]) => (
              <button key={id} type="button" aria-pressed={view === id} onClick={() => chooseView(id)}>{label}</button>
            ))}
          </div>
        </div>

        <aside className="solar-twin__readout" aria-label="Digital twin readings">
          <dl className="solar-facts">
            <div><dt>Panel facing</dt><dd>{bearing(state.azimuthDeg)}</dd></div>
            <div><dt>Panel tilt</dt><dd>{degrees(state.tiltDeg)}{state.fixedTilt ? " · fixed" : ""}</dd></div>
            <div><dt>{target?.kind === "manual" ? "Manual target" : "Sun position"}</dt>
              <dd>{target ? `${bearing(target.azimuthDeg)} · zenith ${degrees(target.zenithDeg)}` : "Unavailable"}</dd></div>
            {target && !target.aboveHorizon && <div><dt>Sun</dt><dd>Below the horizon</dd></div>}
            <div><dt>Turn needed</dt>
              <dd>{state.azimuthErrorDeg === null ? "Unknown"
                : `${Math.abs(state.azimuthErrorDeg).toFixed(1)}° ${state.azimuthErrorDeg >= 0 ? "clockwise" : "anticlockwise"}`}</dd></div>
            {!state.fixedTilt && <div><dt>Tilt change needed</dt><dd>{degrees(state.tiltErrorDeg)}</dd></div>}
            <div><dt>Pointing error</dt><dd>{degrees(state.pointingErrorDeg)}</dd></div>
            <div><dt>Yaw motor</dt><dd>{drive(state.azimuthDrive)}</dd></div>
            <div><dt>Tilt motor</dt><dd>{state.fixedTilt ? "Off (fixed tilt)" : drive(state.tiltDrive)}</dd></div>
            <div><dt>Solar power</dt><dd>{state.powerW === null ? "Unavailable" : `${state.powerW.toFixed(2)} W`}</dd></div>
            {state.travelLimits && (
              <div><dt>Yaw travel</dt>
                <dd>{`${state.travelLimits.minDeg}°–${state.travelLimits.maxDeg}°`}{outsideTravel ? " · target outside" : ""}</dd></div>
            )}
          </dl>
          <ul className="solar-twin__legend">
            <li><i className="solar-twin__key solar-twin__key--facing" />Green arrow: where the panel faces</li>
            <li><i className="solar-twin__key solar-twin__key--sun" />Dashed line: {target?.kind === "manual" ? "manual target" : "sun"} direction</li>
            {state.travelLimits && <li><i className="solar-twin__key solar-twin__key--limit" />Red arc: no-travel zone through north</li>}
            <li><i className="solar-twin__key solar-twin__key--drive" />Amber ring / light: motor drive requested</li>
          </ul>
          <small>
            Drag to orbit, scroll to zoom. The frame is drawn facing south; only the turntable and panel follow
            telemetry, smoothed between 1-second samples. A drive request is not proof of movement.
          </small>
        </aside>
      </div>
    </section>
  );
}
