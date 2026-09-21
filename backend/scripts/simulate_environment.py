"""Publish valid environment telemetry to the local MQTT broker."""

from __future__ import annotations

import argparse
import json
import random
import time
from datetime import datetime, timezone
from uuid import UUID, uuid4

import paho.mqtt.publish as publish


def build_message(site_id: UUID, classroom_id: UUID, sequence: int) -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "message_id": str(uuid4()),
        "occurred_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "site_id": str(site_id),
        "classroom_id": str(classroom_id),
        "producer_id": "simulator-env-01",
        "payload": {
            "sensor_id": "env-01",
            "temperature_c": round(random.uniform(24.0, 28.0), 1),
            "relative_humidity_pct": round(random.uniform(45.0, 75.0), 1),
            "illuminance_lux": round(random.uniform(150.0, 600.0), 1),
            "sample_interval_ms": 3000,
        },
    }



def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="localhost")
    parser.add_argument("--port", type=int, default=1883)
    parser.add_argument("--topic-root", default="aceo/v1")
    parser.add_argument("--site-id", required=True, type=UUID)
    parser.add_argument("--classroom-id", required=True, type=UUID)
    parser.add_argument("--interval", type=float, default=3.0)
    parser.add_argument("--count", type=int, default=0, help="0 publishes until Ctrl+C")
    args = parser.parse_args()

    topic = (
        f"{args.topic_root.rstrip('/')}/{args.site_id}/{args.classroom_id}"
        "/telemetry/environment"
    )
    sequence = 0
    try:
        while args.count == 0 or sequence < args.count:
            sequence += 1
            message = build_message(args.site_id, args.classroom_id, sequence)
            payload = json.dumps(message)
            publish.single(topic, payload, hostname=args.host, port=args.port, qos=1)
            print(f"Published message {sequence}: {payload}", flush=True)
            if args.count == 0 or sequence < args.count:
                time.sleep(args.interval)
    except KeyboardInterrupt:
        print("Simulator stopped.")


if __name__ == "__main__":
    main()
