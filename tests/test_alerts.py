from app.pipeline.alerts import AlertEvent, AlertSource


def test_alert_event_payload_uses_json_ready_source_camera_list():
    event = AlertEvent(
        event_id="alert-1",
        frame_set_id=12,
        relay_run_id=3,
        timestamp_ms=1780502472361,
        severity="warning",
        distance_m=0.62,
        joint="pelvis",
        obstacle_id="unknown",
        ttl_ms=500,
        source=AlertSource(
            processor="mmpose_triangulation",
            camera_devices=("android_device_001", "android_device_002"),
        ),
    )

    payload = event.to_payload()

    assert payload == {
        "event_id": "alert-1",
        "frame_set_id": 12,
        "relay_run_id": 3,
        "timestamp_ms": 1780502472361,
        "severity": "warning",
        "distance_m": 0.62,
        "joint": "pelvis",
        "obstacle_id": "unknown",
        "ttl_ms": 500,
        "source": {
            "processor": "mmpose_triangulation",
            "camera_devices": ["android_device_001", "android_device_002"],
        },
    }
