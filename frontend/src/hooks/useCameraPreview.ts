"use client";

import { useEffect, useState } from "react";
import { streamCameraPreviewFrames } from "@/src/api/camera-preview-service";
import type { UUID } from "@/src/types/contracts";

type PreviewStatus = "stopped" | "connecting" | "live" | "paused" | "offline";
interface PreviewState {
  status: PreviewStatus;
  frameUrl: string | null;
  error: string | null;
  receivedAt: number | null;
}
const STOPPED: PreviewState = { status: "stopped", frameUrl: null, error: null, receivedAt: null };

export function useCameraPreview(classroomId: UUID) {
  const [requested, setRequested] = useState(false);
  const [preview, setPreview] = useState<PreviewState>(STOPPED);
  const apiBaseUrl = process.env.NEXT_PUBLIC_API_BASE_URL ?? "";

  useEffect(() => {
    if (!requested) return;
    let active = true;
    let revision = 0;
    let controller: AbortController | null = null;
    let timer: number | undefined;
    let staleTimer: number | undefined;
    let frameUrl: string | null = null;

    const clearFrame = () => {
      window.clearTimeout(staleTimer);
      if (frameUrl) URL.revokeObjectURL(frameUrl);
      frameUrl = null;
    };

    const connect = async () => {
      if (!active || document.hidden || controller) return;
      const requestRevision = revision;
      const requestController = new AbortController();
      controller = requestController;
      let sessionActive = true;
      let latestFrame: Blob | null = null;
      let rendering = false;
      let renderFailure: Error | null = null;
      let delay = 3_000;
      let watchdog = window.setTimeout(() => requestController.abort(), 10_000);
      const current = () => active && sessionActive && !document.hidden
        && requestRevision === revision && !requestController.signal.aborted;

      const renderLatest = async () => {
        rendering = true;
        try {
          while (latestFrame && current()) {
            const frame = latestFrame;
            latestFrame = null;
            const nextUrl = URL.createObjectURL(frame);
            const image = new Image();
            image.src = nextUrl;
            let decodeTimer: number | undefined;
            let keepUrl = false;
            try {
              await Promise.race([image.decode(), new Promise<never>((_, reject) => {
                decodeTimer = window.setTimeout(() => reject(new Error("The camera frame could not be displayed.")), 2_000);
              })]);
              if (!current()) return;
              const previousUrl = frameUrl;
              frameUrl = nextUrl;
              keepUrl = true;
              setPreview({ status: "live", frameUrl, error: null, receivedAt: Date.now() });
              if (previousUrl) URL.revokeObjectURL(previousUrl);
              window.clearTimeout(staleTimer);
              staleTimer = window.setTimeout(() => {
                if (!active) return;
                clearFrame();
                setPreview({ status: "connecting", frameUrl: null, error: null, receivedAt: null });
              }, 3_000);
            } finally {
              window.clearTimeout(decodeTimer);
              if (!keepUrl) URL.revokeObjectURL(nextUrl);
            }
          }
        } catch {
          renderFailure = new Error("The camera returned an unreadable preview frame.");
          requestController.abort();
        } finally {
          rendering = false;
        }
      };

      try {
        for await (const frame of streamCameraPreviewFrames(apiBaseUrl, classroomId, requestController.signal)) {
          if (!current()) break;
          window.clearTimeout(watchdog);
          watchdog = window.setTimeout(() => requestController.abort(), 3_000);
          // Read the network independently of image decoding. Slow devices keep
          // one pending frame and skip older work instead of building a backlog.
          latestFrame = frame;
          if (!rendering) void renderLatest();
        }
        // The backend renews a bounded stream periodically. Reconnect immediately
        // and leave the last fresh frame visible across that short handover.
        delay = 0;
      } catch (cause) {
        if (!active || document.hidden || requestRevision !== revision) return;
        clearFrame();
        const failure = renderFailure ?? cause;
        setPreview({ status: "offline", frameUrl: null, receivedAt: null,
          error: failure instanceof Error && failure.name !== "AbortError"
            ? failure.message
            : "The camera connection timed out. Retrying automatically.",
        });
      } finally {
        sessionActive = false;
        latestFrame = null;
        window.clearTimeout(watchdog);
        if (controller === requestController) controller = null;
        if (active && !document.hidden) {
          timer = window.setTimeout(connect, requestRevision === revision ? delay : 0);
        }
      }
    };

    const onVisibility = () => {
      revision += 1;
      window.clearTimeout(timer);
      clearFrame();
      if (document.hidden) {
        controller?.abort();
        setPreview({ status: "paused", frameUrl: null, error: null, receivedAt: null });
      } else {
        setPreview({ status: "connecting", frameUrl: null, error: null, receivedAt: null });
        if (!controller) void connect();
      }
    };
    document.addEventListener("visibilitychange", onVisibility);
    timer = window.setTimeout(onVisibility, 0);
    return () => {
      active = false;
      controller?.abort();
      window.clearTimeout(timer);
      clearFrame();
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, [apiBaseUrl, classroomId, requested]);

  return {
    ...preview,
    requested,
    start() {
      setPreview({ status: "connecting", frameUrl: null, error: null, receivedAt: null });
      setRequested(true);
    },
    stop() {
      setRequested(false);
      setPreview(STOPPED);
    },
  };
}
