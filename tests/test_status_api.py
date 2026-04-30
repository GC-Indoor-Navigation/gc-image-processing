from fastapi.testclient import TestClient

from app.buffers.frame_buffer import FrameBufferManager
from app.core.config import Settings
from app.infrastructure.relay_contract import RelayFrame
from app.main import create_app
from app.services.processing import ProcessingService


def create_test_client():
    service = ProcessingService(buffer_manager=FrameBufferManager(buffer_size=120))
    app = create_app(
        settings=Settings(grpc_enabled=False),
        service=service,
    )
    return TestClient(app), service


def test_health_endpoint():
    client, _ = create_test_client()

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_status_endpoint_returns_buffer_state():
    client, service = create_test_client()
    service.handle_relay_frame(
        RelayFrame(
            device_id="camera1",
            timestamp_ms=1000,
            sequence=1,
            content_type="image/jpeg",
            image_bytes=b"frame",
        )
    )

    response = client.get("/status")

    assert response.status_code == 200
    body = response.json()
    assert body["grpc"]["enabled"] is False
    assert body["grpc"]["running"] is False
    assert body["buffer"]["camera_count"] == 1
    assert body["buffer"]["received_count"] == 1
    assert body["buffer"]["cameras"][0]["device_id"] == "camera1"


def test_cameras_endpoint_returns_camera_statuses():
    client, service = create_test_client()
    service.handle_relay_frame(
        RelayFrame(
            device_id="camera1",
            timestamp_ms=1000,
            sequence=1,
            content_type="image/jpeg",
            image_bytes=b"frame",
        )
    )

    response = client.get("/cameras")

    assert response.status_code == 200
    assert response.json()[0]["received_count"] == 1


def test_camera_endpoint_returns_single_camera_status():
    client, service = create_test_client()
    service.handle_relay_frame(
        RelayFrame(
            device_id="camera1",
            timestamp_ms=1000,
            sequence=1,
            content_type="image/jpeg",
            image_bytes=b"frame",
        )
    )

    response = client.get("/cameras/camera1")

    assert response.status_code == 200
    assert response.json()["device_id"] == "camera1"
    assert response.json()["received_count"] == 1


def test_camera_endpoint_returns_404_for_unknown_camera():
    client, _ = create_test_client()

    response = client.get("/cameras/missing")

    assert response.status_code == 404
    assert response.json()["detail"] == "camera not found: missing"


def test_latest_frame_endpoint_returns_metadata():
    client, service = create_test_client()
    service.handle_relay_frame(
        RelayFrame(
            device_id="camera1",
            timestamp_ms=1000,
            sequence=1,
            content_type="image/jpeg",
            image_bytes=b"frame",
            file_path="storage/camera1/1.jpg",
        )
    )

    response = client.get("/cameras/camera1/latest")

    assert response.status_code == 200
    body = response.json()
    assert body["device_id"] == "camera1"
    assert body["sequence"] == 1
    assert body["image_size"] == 5
    assert body["source_file_path"] == "storage/camera1/1.jpg"


def test_latest_frame_image_endpoint_returns_bytes():
    client, service = create_test_client()
    service.handle_relay_frame(
        RelayFrame(
            device_id="camera1",
            timestamp_ms=1000,
            sequence=1,
            content_type="image/jpeg",
            image_bytes=b"frame",
        )
    )

    response = client.get("/cameras/camera1/latest/image")

    assert response.status_code == 200
    assert response.content == b"frame"
    assert response.headers["content-type"] == "image/jpeg"


def test_latest_frame_endpoint_returns_404_for_missing_camera():
    client, _ = create_test_client()

    response = client.get("/cameras/missing/latest")

    assert response.status_code == 404
    assert response.json()["detail"] == "latest frame not found: missing"


def test_pipeline_status_endpoint_returns_queue_and_worker_state():
    client, _ = create_test_client()

    response = client.get("/pipeline/status")

    assert response.status_code == 200
    body = response.json()
    assert body["sync"]["enabled"] is False
    assert body["queue"]["queue_size"] == 0
    assert body["worker"]["enabled"] is True
