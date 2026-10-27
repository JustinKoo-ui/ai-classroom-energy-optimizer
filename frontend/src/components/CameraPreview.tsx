"use client";

import { useCameraPreview } from "@/src/hooks/useCameraPreview";
import type { UUID } from "@/src/types/contracts";

const STATUS_LABELS = {
  stopped: "Preview stopped",
  connecting: "Connecting",
  live: "Live",
  paused: "Paused",
  offline: "Offline · retrying",
};

export function CameraPreview({ classroomId }: { classroomId: UUID }) {
  const preview = useCameraPreview(classroomId);
  return (
    <section id="camera" className="camera-section" aria-labelledby="camera-title">
      <div className="section-heading">
        <div><p className="eyebrow">Classroom camera</p><h2 id="camera-title">Live camera preview</h2></div>
        <p>Up to 15 frames/sec · no audio · no recording</p>
      </div>
      <div className="camera-card">
        <div className="camera-card__viewport">
          {preview.frameUrl ? (
            // Live frames are short-lived object URLs decoded by the hook.
            // eslint-disable-next-line @next/next/no-img-element
            <img src={preview.frameUrl} alt="Live view from the classroom camera" width={640} height={360} />
          ) : (
            <div className="camera-card__placeholder">
              <svg viewBox="0 0 40 40" width="48" height="48" fill="none" aria-hidden="true">
                <rect x="5" y="11" width="23" height="20" rx="5" stroke="currentColor" strokeWidth="1.5" />
                <path d="m28 18 7-4v14l-7-4M12 7h9" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
                <circle cx="16.5" cy="21" r="4" stroke="currentColor" strokeWidth="1.5" />
              </svg>
              <strong>{preview.status === "stopped" ? "A live look at your classroom" : preview.status === "paused" ? "Preview paused while this tab is hidden" : preview.status === "offline" ? "Waiting for the camera" : "Connecting to the classroom camera…"}</strong>
              <span>{preview.status === "stopped" ? "Start the preview when you need it." : "Frames appear only while the camera connection is available."}</span>
            </div>
          )}
          <span className={`camera-status camera-status--${preview.status}`} role="status" aria-live="polite">
            <i aria-hidden="true" />{STATUS_LABELS[preview.status]}
          </span>
        </div>
        <div className="camera-card__details">
          <div>
            <strong>View on demand</strong>
            <p>The preview pauses when you leave this tab. Frames are held temporarily in memory.</p>
            {preview.error && <p className="camera-card__error" role="status">{preview.error}</p>}
          </div>
          <button className="camera-card__button" type="button" onClick={preview.requested ? preview.stop : preview.start} aria-pressed={preview.requested}>
            {preview.requested ? "Stop preview" : "Start preview"}
          </button>
        </div>
      </div>
    </section>
  );
}
