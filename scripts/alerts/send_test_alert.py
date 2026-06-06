from __future__ import annotations

import argparse
import time
from uuid import uuid4

from app.pipeline.alerts import AlertEvent, AlertPublisher, AlertSource


def main() -> None:
    args = parse_args()
    camera_devices = tuple(
        item.strip()
        for item in args.camera_devices.split(",")
        if item.strip()
    )
    if not camera_devices:
        raise SystemExit("--camera-devices must contain at least one device")

    publisher = AlertPublisher(
        enabled=True,
        target_url=args.target_url,
        timeout_sec=args.timeout_sec,
    )

    for index in range(args.repeat):
        event = AlertEvent(
            event_id=args.event_id or f"test-alert-{uuid4().hex[:12]}",
            frame_set_id=args.frame_set_id + index,
            relay_run_id=args.relay_run_id,
            timestamp_ms=int(time.time() * 1000),
            severity=args.severity,
            distance_m=args.distance_m,
            joint=args.joint,
            obstacle_id=args.obstacle_id,
            ttl_ms=args.ttl_ms,
            source=AlertSource(
                processor=args.processor,
                camera_devices=camera_devices,
            ),
        )
        payload = event.to_payload()
        print(f"[send] {payload}")
        if not publisher.publish(event):
            raise SystemExit(f"alert publish failed: {publisher.status()}")
        print(f"[ok] sent event_id={event.event_id}")
        if index < args.repeat - 1:
            time.sleep(args.interval_sec)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Send a Processing Server test alert to the Stream Server.",
    )
    parser.add_argument(
        "--target-url",
        required=True,
        help="Stream Server alert endpoint, e.g. http://127.0.0.1:8000/internal/processing-alerts",
    )
    parser.add_argument("--severity", choices=["info", "warning", "danger"], default="danger")
    parser.add_argument("--event-id", default="")
    parser.add_argument("--frame-set-id", type=int, default=1)
    parser.add_argument("--relay-run-id", type=int, default=1)
    parser.add_argument("--distance-m", type=float, default=0.2)
    parser.add_argument("--joint", default="left_ankle")
    parser.add_argument("--obstacle-id", default="test-obstacle")
    parser.add_argument("--ttl-ms", type=int, default=3000)
    parser.add_argument("--processor", default="test_alert_sender")
    parser.add_argument(
        "--camera-devices",
        default="android_device_001,android_device_002,android_device_003",
    )
    parser.add_argument("--timeout-sec", type=float, default=2.0)
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--interval-sec", type=float, default=1.0)
    return parser.parse_args()


if __name__ == "__main__":
    main()
