# Computer-vision worker

This worker runs a Roboflow video workflow and reports aggregate per-frame
counts. It does not save frames or track IDs, connect to MQTT, or control
appliances. The workflow must return outputs named `tracked_people` and
`raw_predictions`; configure its detector for people and include a tracker
(ByteTrack) before relying on the `tracked_people` count.

## Setup

Use Python 3.11 or newer in an isolated environment. From this directory:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e .
```

The worker pins `supervision` to `0.29.1` because the Roboflow workflow's
ByteTrack step calls its `ByteTrack` API, which is absent in newer releases.
Reinstall the worker dependencies after changing the package configuration.

Set these environment variables in your local shell or an untracked `.env`
file. Never commit a real API key.

| Variable | Default | Purpose |
|---|---|---|
| `ROBOFLOW_API_KEY` | required | Key used by the Roboflow Inference SDK |
| `ROBOFLOW_WORKSPACE` | `justin-koo` | Roboflow workspace slug |
| `ROBOFLOW_WORKFLOW_ID` | `custom-workflow` | Workflow to execute |
| `VIDEO_SOURCE` / `CAMERA_SOURCE` | `classroom_sample.mp4` | Video file, stream URL, or numeric camera index; `CAMERA_SOURCE` is the local-network camera alias |
| `SITE_ID` | `c7ef6d75-7c12-461a-bb49-73f917c3357d` | Site identifier used in the MQTT envelope |
| `CLASSROOM_ID` | `26e97d46-7a34-4439-922a-83684bda557d` | Classroom identifier used in the MQTT envelope |
| `CAMERA_ID` | `camera-01` | Camera identifier published in the occupancy message |
| `MAX_FPS` | `10` | Maximum frame processing rate |
| `MQTT_HOST` | `localhost` | MQTT broker host |
| `MQTT_PORT` | `1883` | MQTT broker port |
| `MQTT_TOPIC_ROOT` | `aceo/v1` | Topic root used by the backend contract |
| `MQTT_USERNAME` / `MQTT_PASSWORD` | optional | Broker credentials |
| `MQTT_PUBLISH_OCCUPANCY` | `true` | Enables aggregate occupancy MQTT publishing |
| `LOG_LEVEL` | `INFO` | Python logging level |

Run from `cv_service`:

```powershell
aceo-cv-worker
```

To use a camera index, set `VIDEO_SOURCE=0` or `CAMERA_SOURCE=0`. For a local
network camera, set `CAMERA_SOURCE` to the camera's supported RTSP stream URL
(such as `rtsp://admin:REPLACE_WITH_PASSWORD@192.168.1.50:554/stream1`). Use the
camera's own supported stream configuration and confirm the laptop can reach it
on the same LAN before enabling the worker. Confirm the workflow's inference
execution mode is appropriate for the deployment: using the Inference SDK does
not by itself guarantee that every workflow block runs locally.

The callback logs only the frame number and aggregate tracked/raw counts. A
missing or unrecognized `tracked_people` output is reported as `unknown`, not
as zero occupants. The workflow must be checked against representative video
before treating its count as an occupancy reading.

## Current integration boundary

The worker publishes aggregate occupancy observations to the MQTT topic defined
in `../docs/mqtt.md`. The backend remains the authoritative state source, and
frontend state must always flow through validated backend messages. Stale/lost
video must become `unknown`, not vacant.

Run the unit tests from this directory:

```powershell
python -m unittest discover -s tests -v
```
