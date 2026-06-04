import logging

from app.pipeline.alerts import (
    AlertEvent,
    AlertPublisher,
    AlertSource,
    NoOpProximityAlertEvaluator,
)


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


def test_alert_publisher_skips_when_disabled():
    sent = []
    publisher = AlertPublisher(
        enabled=False,
        target_url="http://stream/internal/processing-alerts",
        sender=lambda url, payload, timeout_sec: sent.append(payload),
    )

    published = publisher.publish(_alert_event())

    assert published is False
    assert sent == []
    assert publisher.status() == {
        "enabled": False,
        "target_configured": True,
        "sent_count": 0,
        "failed_count": 0,
        "skipped_count": 1,
        "last_event_id": "alert-1",
        "last_error": None,
    }


def test_alert_publisher_sends_payload_when_enabled():
    sent = []

    def sender(url, payload, timeout_sec):
        sent.append((url, payload, timeout_sec))

    publisher = AlertPublisher(
        enabled=True,
        target_url="http://stream/internal/processing-alerts",
        timeout_sec=0.25,
        sender=sender,
    )

    published = publisher.publish(_alert_event())

    assert published is True
    assert sent == [
        (
            "http://stream/internal/processing-alerts",
            _alert_event().to_payload(),
            0.25,
        )
    ]
    assert publisher.status()["sent_count"] == 1
    assert publisher.status()["failed_count"] == 0


def test_alert_publisher_records_missing_target_as_failure(caplog):
    publisher = AlertPublisher(enabled=True, target_url="")

    with caplog.at_level(logging.WARNING):
        published = publisher.publish(_alert_event())

    assert published is False
    assert publisher.status()["failed_count"] == 1
    assert publisher.status()["last_error"] == "alert target url is not configured"
    assert "alert publish skipped" in caplog.text


def test_alert_publisher_records_sender_failure_without_raising(caplog):
    def sender(url, payload, timeout_sec):
        raise RuntimeError("stream server unavailable")

    publisher = AlertPublisher(
        enabled=True,
        target_url="http://stream/internal/processing-alerts",
        sender=sender,
    )

    with caplog.at_level(logging.ERROR):
        published = publisher.publish(_alert_event())

    assert published is False
    assert publisher.status()["failed_count"] == 1
    assert publisher.status()["last_error"] == "stream server unavailable"
    assert "alert publish failed event_id=alert-1" in caplog.text


def test_noop_proximity_alert_evaluator_returns_no_alert():
    evaluator = NoOpProximityAlertEvaluator()

    alert = evaluator.evaluate(
        processing_result=object(),
        skeleton_result=None,
        ttl_ms=500,
        processor_name="mmpose_triangulation",
        camera_devices=("camera1",),
    )

    assert alert is None


def _alert_event():
    return AlertEvent(
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
