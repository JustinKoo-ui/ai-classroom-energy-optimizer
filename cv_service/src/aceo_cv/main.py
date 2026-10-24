from __future__ import annotations

import logging
import os

from .mqtt import OccupancyPublisher
from .occupancy import workflow_output_count, workflow_result_shape

logger = logging.getLogger("cv_service")

_occupancy_publisher: OccupancyPublisher | None = None


def _first_setting(*names: str, default: str | None = None) -> str | None:
    for name in names:
        value = os.getenv(name)
        if value is not None and value != "":
            return value
    return default


def on_prediction(result: object, video_frame: object) -> None:
    frame_id = getattr(video_frame, "frame_id", "unknown")
    tracked = workflow_output_count(result, "tracked_people")
    detected = workflow_output_count(result, "raw_predictions")

    logger.info(
        "frame=%s tracked_people=%s raw_detections=%s",
        frame_id,
        tracked if tracked is not None else "unknown",
        detected if detected is not None else "unknown",
    )
    if tracked is None or detected is None:
        logger.warning("Workflow outputs missing or unrecognized; result_shape=%s",
                       workflow_result_shape(result))

    if _occupancy_publisher is None:
        return
    if tracked is None:
        return

    frame_sequence = 0
    if isinstance(frame_id, int):
        frame_sequence = frame_id
    elif isinstance(frame_id, str):
        try:
            frame_sequence = int(frame_id)
        except ValueError:
            frame_sequence = 0

    try:
        _occupancy_publisher.publish_observation(
            count=tracked,
            confidence=0.97,
            frame_sequence=frame_sequence,
        )
    except Exception:
        logger.exception("Failed to publish tracked occupancy count")


def _required_setting(name: str, default: str | None = None) -> str:
    value = _first_setting(name, default=default)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def _video_reference(value: str) -> str | int:
    if value.isdecimal():
        return int(value)
    return value


def _resolve_video_source() -> str | int:
    value = _first_setting("VIDEO_SOURCE", "CAMERA_SOURCE", default="classroom_sample.mp4")
    if value is None:
        return "classroom_sample.mp4"
    return _video_reference(value)


def _feature_enabled(name: str, default: bool = True) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() not in {"0", "false", "no", "off"}


def _video_source_type(value: str | int) -> str:
    if isinstance(value, int):
        return "camera_index"
    if value.lower().startswith(("rtsp://", "rtsps://", "http://", "https://")):
        return "network_stream"
    return "file"


def main() -> None:
    from dotenv import load_dotenv

    load_dotenv()

    from inference import InferencePipeline

    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO").upper())

    api_key = _required_setting("ROBOFLOW_API_KEY")
    workspace_name = _required_setting("ROBOFLOW_WORKSPACE", "justin-koo")
    workflow_id = _required_setting("ROBOFLOW_WORKFLOW_ID", "custom-workflow")
    video_reference = _resolve_video_source()
    max_fps = int(_required_setting("MAX_FPS", "10"))
    if max_fps <= 0:
        raise ValueError("MAX_FPS must be a positive integer")

    site_id = _first_setting("SITE_ID", default="c7ef6d75-7c12-461a-bb49-73f917c3357d")
    classroom_id = _first_setting(
        "CLASSROOM_ID",
        default="26e97d46-7a34-4439-922a-83684bda557d",
    )
    mqtt_enabled = _feature_enabled("MQTT_PUBLISH_OCCUPANCY", default=True)
    if mqtt_enabled:
        global _occupancy_publisher
        _occupancy_publisher = OccupancyPublisher(
            host=_first_setting("MQTT_HOST", default="localhost") or "localhost",
            port=int(_first_setting("MQTT_PORT", default="1883") or "1883"),
            topic_root=_first_setting("MQTT_TOPIC_ROOT", default="aceo/v1") or "aceo/v1",
            client_id=_first_setting("MQTT_CLIENT_ID", default="aceo-cv-worker")
            or "aceo-cv-worker",
            site_id=site_id or "c7ef6d75-7c12-461a-bb49-73f917c3357d",
            classroom_id=classroom_id or "26e97d46-7a34-4439-922a-83684bda557d",
            producer_id=_first_setting("MQTT_PRODUCER_ID", default="cv-worker-local")
            or "cv-worker-local",
            camera_id=_first_setting("CAMERA_ID", default="camera-01") or "camera-01",
            username=_first_setting("MQTT_USERNAME"),
            password=_first_setting("MQTT_PASSWORD"),
            tls_enabled=_feature_enabled("MQTT_TLS_ENABLED", default=False),
        )
        _occupancy_publisher.connect()

    pipeline = InferencePipeline.init_with_workflow(
        api_key=api_key,
        workspace_name=workspace_name,
        workflow_id=workflow_id,
        video_reference=video_reference,
        max_fps=max_fps,
        on_prediction=on_prediction,
    )

    logger.info(
        "Starting occupancy inference workspace=%s workflow=%s max_fps=%d source_type=%s",
        workspace_name,
        workflow_id,
        max_fps,
        _video_source_type(video_reference),
    )
    pipeline.start()
    pipeline.join()


if __name__ == "__main__":
    main()
