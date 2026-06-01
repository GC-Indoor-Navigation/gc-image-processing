from fastapi.testclient import TestClient

from app.buffers.frame_buffer import FrameBufferManager
from app.core.config import Settings
from app.infrastructure.relay_contract import RelayFrame
from app.models.frame import SynchronizedFrameSet
from app.main import create_app
from app.pipeline.processor import ProcessingResult
from app.pipeline.queue import ProcessingQueue
from app.services.processing import ProcessingService
from app.sync.matcher import SyncMatcher


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
    assert body["relay_path"]["primary_method"] == "StreamFrameSets"
    assert body["relay_path"]["raw_stream_frames_mode"] == "legacy_fallback"
    assert body["relay_path"]["raw_sync_enabled"] is False
    assert body["sync"]["enabled"] is False
    assert body["sync"]["matched_count"] == 0
    assert body["sync"]["last_missing_cameras"] == []
    assert body["relay_frame_sets"]["accepted_count"] == 0
    assert body["relay_frame_sets"]["duplicate_count"] == 0
    assert body["queue"]["queue_size"] == 0
    assert body["worker"]["enabled"] is True
    assert body["worker"]["last_result"] is None


def test_latest_pipeline_result_endpoint_returns_empty_state():
    client, _ = create_test_client()

    response = client.get("/pipeline/results/latest")

    assert response.status_code == 200
    body = response.json()
    assert body["available"] is False
    assert body["processor"] == "PlaceholderMotionCaptureProcessor"
    assert body["processing_result"] is None
    assert body["result"] is None
    assert body["last_error"] is None


def test_result_history_and_summary_endpoints_return_saved_results(tmp_path):
    service = ProcessingService(buffer_manager=FrameBufferManager(buffer_size=120))
    app = create_app(
        settings=Settings(
            grpc_enabled=False,
            result_storage_enabled=True,
            result_storage_dir=tmp_path,
        ),
        service=service,
    )
    client = TestClient(app)
    result_store = app.state.result_store
    result_store.save(
        SynchronizedFrameSet(
            frame_set_id=10,
            anchor_timestamp_ms=1000,
            max_delta_ms=8,
            relay_run_id=1,
            frames={},
        ),
        ProcessingResult(
            frame_set_id=10,
            status="mmpose_triangulated",
            camera_count=3,
            started_at=1.0,
            finished_at=2.0,
            elapsed_ms=1000.0,
        ),
        {
            "frame_set_id": 10,
            "anchor_timestamp_ms": 1000,
            "max_delta_ms": 8,
            "num_valid_joints": 17,
            "avg_reproj_error_px": 2.5,
            "joints_world": {},
            "source_frames": {},
        },
    )

    history_response = client.get("/pipeline/results/history?limit=5")
    summary_response = client.get("/pipeline/results/summary")

    assert history_response.status_code == 200
    assert history_response.json()[0]["frame_set_id"] == 10
    assert history_response.json()[0]["num_valid_joints"] == 17
    assert summary_response.status_code == 200
    assert summary_response.json()["run_count"] == 1
    assert summary_response.json()["runs"][0]["result_count"] == 1
    assert summary_response.json()["runs"][0]["avg_reproj_error_px"] == 2.5


def test_live_skeleton_viewer_endpoint_returns_html():
    client, _ = create_test_client()

    response = client.get("/pipeline/results/viewer")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "Live 3D Skeleton" in response.text
    assert "/pipeline/results/latest" in response.text


def test_latest_pipeline_result_endpoint_returns_triangulation_result():
    client, _ = create_test_client()
    worker = client.app.state.motion_capture_worker
    worker.last_result = ProcessingResult(
        frame_set_id=10,
        status="mmpose_triangulated",
        camera_count=3,
        started_at=1.0,
        finished_at=2.0,
        elapsed_ms=1000.0,
    )
    worker.processor.last_skeleton_result = {
        "frame_set_id": 10,
        "anchor_timestamp_ms": 1000,
        "max_delta_ms": 8,
        "num_valid_joints": 17,
        "avg_reproj_error_px": 2.5,
        "joints_world": {
            "nose": {
                "xyz": [1.0, 2.0, 3.0],
                "score": 0.9,
                "reproj_error_px": 1.5,
                "reproj_error_by_camera_px": {"Camera1": 1.0},
                "used_cameras": ["Camera1", "Camera2"],
            }
        },
        "joints_camera": {"Camera1": {"nose": [0.1, 0.2, 0.3]}},
        "camera_centers_world": {"Camera1": [0.0, 0.0, 0.0]},
        "source_frames": {
            "Camera1": {
                "device_id": "camera1",
                "timestamp_ms": 1000,
                "sequence": 1,
                "source_file_path": None,
                "source_frame_id": 101,
            }
        },
    }

    response = client.get("/pipeline/results/latest")

    assert response.status_code == 200
    body = response.json()
    assert body["available"] is True
    assert body["processing_result"]["status"] == "mmpose_triangulated"
    assert body["result"]["frame_set_id"] == 10
    assert body["result"]["num_valid_joints"] == 17
    assert body["result"]["joints_world"]["nose"]["xyz"] == [1.0, 2.0, 3.0]


def test_recent_frame_sets_endpoint_returns_metadata_without_bytes():
    buffer_manager = FrameBufferManager(buffer_size=120)
    queue = ProcessingQueue()
    matcher = SyncMatcher(
        buffer_manager=buffer_manager,
        expected_cameras=["camera1", "camera2"],
        window_ms=30,
    )
    service = ProcessingService(
        buffer_manager=buffer_manager,
        sync_matcher=matcher,
        processing_queue=queue,
    )
    app = create_app(
        settings=Settings(
            grpc_enabled=False,
            sync_enabled=True,
            expected_cameras=("camera1", "camera2"),
        ),
        service=service,
    )
    app.state.sync_matcher = matcher
    client = TestClient(app)

    service.handle_relay_frame(
        RelayFrame("camera1", 1000, 1, "image/jpeg", b"frame-1")
    )
    service.handle_relay_frame(
        RelayFrame("camera2", 1010, 1, "image/jpeg", b"frame-2")
    )

    response = client.get("/pipeline/recent-frame-sets")

    assert response.status_code == 200
    body = response.json()
    assert body[0]["frame_set_id"] == 1
    assert set(body[0]["frames"]) == {"camera1", "camera2"}
    assert body[0]["frames"]["camera1"]["image_size"] == 7
    assert "image_bytes" not in body[0]["frames"]["camera1"]
