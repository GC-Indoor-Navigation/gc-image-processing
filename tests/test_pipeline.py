from time import sleep

from app.buffers.frame_buffer import FrameBufferManager
from app.infrastructure.relay_contract import (
    RelayFrame,
    RelayFrameSet,
    RelayFrameSetFrame,
)
from app.models.frame import StoredFrame, SynchronizedFrameSet
from app.pipeline.external_processor import ExternalMotionCaptureProcessor
from app.pipeline.input_adapter import MotionCaptureInputAdapter
from app.pipeline.processor import PlaceholderMotionCaptureProcessor, ProcessingResult
from app.pipeline.queue import ProcessingQueue
from app.pipeline.result_store import JsonlTriangulationResultStore
from app.pipeline.worker import MotionCaptureWorker
from app.services.processing import ProcessingService
from app.sync.matcher import SyncMatcher


def test_processing_queue_tracks_enqueue_and_dequeue():
    queue = ProcessingQueue()
    frame_set = SynchronizedFrameSet(
        frame_set_id=1,
        anchor_timestamp_ms=1000,
        max_delta_ms=0,
        frames={},
    )

    queue.enqueue(frame_set)
    dequeued = queue.get(timeout=0.01)

    assert dequeued == frame_set
    assert queue.status() == {
        "queue_size": 0,
        "enqueued_count": 1,
        "dequeued_count": 1,
    }


def test_motion_capture_worker_processes_queued_frame_set():
    queue = ProcessingQueue()
    worker = MotionCaptureWorker(processing_queue=queue)
    frame_set = SynchronizedFrameSet(
        frame_set_id=1,
        anchor_timestamp_ms=1000,
        max_delta_ms=0,
        frames={},
    )

    worker.start()
    try:
        queue.enqueue(frame_set)
        for _ in range(100):
            if worker.status()["processed_count"] == 1:
                break
            sleep(0.01)
        assert worker.status()["processed_count"] == 1
        assert worker.status()["last_processed_frame_set_id"] == 1
        assert worker.status()["last_result"]["frame_set_id"] == 1
        assert worker.status()["last_result"]["status"] == "placeholder_processed"
        assert worker.status()["last_result"]["camera_count"] == 0
    finally:
        worker.stop()


def test_motion_capture_worker_delegates_to_processor():
    class RecordingProcessor:
        def __init__(self):
            self.received = []

        def process(self, processing_input):
            self.received.append(processing_input)
            return ProcessingResult(
                frame_set_id=processing_input.frame_set_id,
                status="processed_by_test",
                camera_count=len(processing_input.frames),
                started_at=1.0,
                finished_at=2.0,
                elapsed_ms=1000.0,
            )

    queue = ProcessingQueue()
    processor = RecordingProcessor()
    worker = MotionCaptureWorker(processing_queue=queue, processor=processor)
    frame_set = SynchronizedFrameSet(
        frame_set_id=2,
        anchor_timestamp_ms=1000,
        max_delta_ms=0,
        frames={},
    )

    worker.start()
    try:
        queue.enqueue(frame_set)
        for _ in range(100):
            if worker.status()["processed_count"] == 1:
                break
            sleep(0.01)
        assert len(processor.received) == 1
        assert processor.received[0].frame_set_id == frame_set.frame_set_id
        assert worker.status()["last_result"]["status"] == "processed_by_test"
        assert worker.status()["last_processed_at"] == 2.0
    finally:
        worker.stop()


def test_motion_capture_input_adapter_preserves_frame_metadata():
    adapter = MotionCaptureInputAdapter()
    frame_set = SynchronizedFrameSet(
        frame_set_id=3,
        anchor_timestamp_ms=1000,
        max_delta_ms=10,
        frames={
            "camera1": StoredFrame(
                device_id="camera1",
                timestamp_ms=1000,
                sequence=7,
                content_type="image/jpeg",
                image_bytes=b"frame",
                image_size=5,
                source_file_path="storage/camera1/7.jpg",
                source_frame_id=77,
            )
        },
    )

    processing_input = adapter.from_frame_set(frame_set)

    assert processing_input.frame_set_id == 3
    assert processing_input.anchor_timestamp_ms == 1000
    assert processing_input.frames["camera1"].timestamp_ms == 1000
    assert processing_input.frames["camera1"].image_bytes == b"frame"
    assert (
        processing_input.frames["camera1"].source_file_path
        == "storage/camera1/7.jpg"
    )
    assert processing_input.frames["camera1"].source_frame_id == 77


def test_external_motion_capture_processor_wraps_runner():
    received = []

    def runner(processing_input):
        received.append(processing_input)
        return {"status": "external_ok"}

    processor = ExternalMotionCaptureProcessor(runner=runner)
    processing_input = MotionCaptureInputAdapter().from_frame_set(
        SynchronizedFrameSet(
            frame_set_id=4,
            anchor_timestamp_ms=1000,
            max_delta_ms=10,
            frames={},
        )
    )

    result = processor.process(processing_input)

    assert received == [processing_input]
    assert result.frame_set_id == 4
    assert result.status == "external_ok"
    assert result.camera_count == 0
    assert result.elapsed_ms >= 0


def test_placeholder_motion_capture_processor_uses_input_metadata():
    processor = PlaceholderMotionCaptureProcessor()
    processing_input = MotionCaptureInputAdapter().from_frame_set(
        SynchronizedFrameSet(
            frame_set_id=5,
            anchor_timestamp_ms=1000,
            max_delta_ms=10,
            frames={},
        )
    )

    result = processor.process(processing_input)

    assert result.frame_set_id == 5
    assert result.status == "placeholder_processed"
    assert result.camera_count == 0


def test_processing_service_enqueues_synchronized_frame_set():
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

    service.handle_relay_frame(
        RelayFrame("camera1", 1000, 1, "image/jpeg", b"frame-1")
    )
    service.handle_relay_frame(
        RelayFrame("camera2", 1010, 1, "image/jpeg", b"frame-2")
    )

    assert queue.status()["enqueued_count"] == 1
    frame_set = queue.get(timeout=0.01)
    assert frame_set is not None
    assert set(frame_set.frames) == {"camera1", "camera2"}


def test_processing_service_enqueues_relay_frame_set_without_sync_matcher():
    queue = ProcessingQueue()
    service = ProcessingService(
        buffer_manager=FrameBufferManager(buffer_size=120),
        processing_queue=queue,
    )

    result = service.handle_relay_frame_set(
        RelayFrameSet(
            frame_set_id=10,
            anchor_timestamp_ms=1000,
            max_delta_ms=10,
            frames=(
                RelayFrameSetFrame(
                    "camera1",
                    1000,
                    1,
                    "image/jpeg",
                    b"frame-1",
                    frame_id=101,
                ),
                RelayFrameSetFrame("camera2", 1010, 1, "image/jpeg", b"frame-2"),
            ),
        )
    )

    assert result is not None
    assert queue.status()["enqueued_count"] == 1
    queued = queue.get(timeout=0.01)
    assert queued is not None
    assert queued.frame_set_id == 10
    assert set(queued.frames) == {"camera1", "camera2"}
    assert queued.frames["camera1"].source_frame_id == 101
    assert service.relay_frame_set_status() == {
        "accepted_count": 1,
        "duplicate_count": 0,
        "last_frame_set_id": 10,
        "current_run_id": 1,
        "run_idle_reset_sec": 5.0,
    }


def test_processing_service_ignores_duplicate_relay_frame_set_id():
    queue = ProcessingQueue()
    service = ProcessingService(
        buffer_manager=FrameBufferManager(buffer_size=120),
        processing_queue=queue,
    )
    frame_set = RelayFrameSet(
        frame_set_id=10,
        anchor_timestamp_ms=1000,
        max_delta_ms=10,
        frames=(RelayFrameSetFrame("camera1", 1000, 1, "image/jpeg", b"frame"),),
    )

    assert service.handle_relay_frame_set(frame_set) is not None
    assert service.handle_relay_frame_set(frame_set) is None

    assert queue.status()["enqueued_count"] == 1
    assert service.relay_frame_set_status()["duplicate_count"] == 1


def test_processing_service_starts_new_relay_run_after_idle_reset(monkeypatch):
    ticks = iter([0.0, 10.0])
    monkeypatch.setattr("app.services.processing.monotonic", lambda: next(ticks))
    queue = ProcessingQueue()
    service = ProcessingService(
        buffer_manager=FrameBufferManager(buffer_size=120),
        processing_queue=queue,
        relay_run_idle_reset_sec=5.0,
    )
    first = RelayFrameSet(
        frame_set_id=18,
        anchor_timestamp_ms=1000,
        max_delta_ms=10,
        frames=(RelayFrameSetFrame("camera1", 1000, 1, "image/jpeg", b"frame"),),
    )
    second = RelayFrameSet(
        frame_set_id=1,
        anchor_timestamp_ms=2000,
        max_delta_ms=10,
        frames=(RelayFrameSetFrame("camera1", 2000, 1, "image/jpeg", b"frame"),),
    )

    assert service.handle_relay_frame_set(first) is not None
    assert service.handle_relay_frame_set(second) is not None

    assert queue.status()["enqueued_count"] == 2
    assert queue.get(timeout=0.01).relay_run_id == 1
    assert queue.get(timeout=0.01).relay_run_id == 2
    assert service.relay_frame_set_status()["current_run_id"] == 2


def test_jsonl_result_store_writes_compact_triangulation_summary(tmp_path):
    frame_set = SynchronizedFrameSet(
        frame_set_id=3,
        anchor_timestamp_ms=1000,
        max_delta_ms=10,
        relay_run_id=2,
        frames={},
    )
    processing_result = ProcessingResult(
        frame_set_id=3,
        status="mmpose_triangulated",
        camera_count=3,
        started_at=1.0,
        finished_at=2.0,
        elapsed_ms=1000.0,
    )
    skeleton_result = {
        "frame_set_id": 3,
        "anchor_timestamp_ms": 1000,
        "max_delta_ms": 10,
        "num_valid_joints": 1,
        "avg_reproj_error_px": 12.5,
        "joints_world": {
            "nose": {
                "xyz": [1.0, 2.0, 3.0],
                "score": 0.9,
                "reproj_error_px": 4.5,
                "reproj_error_by_camera_px": {"Camera1": 5.0},
            }
        },
        "joints_camera": {"Camera1": {"nose": [1.0, 2.0, 3.0]}},
        "keypoints_by_camera": {"Camera1": {"nose": [10.0, 20.0]}},
        "source_frames": {
            "Camera1": {
                "device_id": "android_device_001",
                "timestamp_ms": 1000,
                "sequence": 7,
                "source_file_path": "storage/camera1/frame.jpg",
                "source_frame_id": 77,
            }
        },
    }
    store = JsonlTriangulationResultStore(tmp_path)

    path = store.save(frame_set, processing_result, skeleton_result)

    import json

    saved = json.loads(path.read_text(encoding="utf-8").strip())
    summary = saved["triangulation_summary"]
    assert saved["relay_run_id"] == 2
    assert summary["joints_world"] == {"nose": [1.0, 2.0, 3.0]}
    assert summary["joint_scores"] == {"nose": 0.9}
    assert summary["joint_reproj_error_px"] == {"nose": 4.5}
    assert "joints_camera" not in summary
    assert "keypoints_by_camera" not in summary


def test_jsonl_result_store_reads_history_and_summary(tmp_path):
    store = JsonlTriangulationResultStore(tmp_path)
    processing_result = ProcessingResult(
        frame_set_id=1,
        status="mmpose_triangulated",
        camera_count=3,
        started_at=1.0,
        finished_at=2.0,
        elapsed_ms=1000.0,
    )
    for frame_set_id, reproj_error in [(1, 12.0), (2, 18.0)]:
        store.save(
            SynchronizedFrameSet(
                frame_set_id=frame_set_id,
                anchor_timestamp_ms=1000 + frame_set_id,
                max_delta_ms=10,
                relay_run_id=1,
                frames={},
            ),
            ProcessingResult(
                frame_set_id=frame_set_id,
                status=processing_result.status,
                camera_count=processing_result.camera_count,
                started_at=processing_result.started_at,
                finished_at=processing_result.finished_at,
                elapsed_ms=processing_result.elapsed_ms,
            ),
            {
                "frame_set_id": frame_set_id,
                "anchor_timestamp_ms": 1000 + frame_set_id,
                "max_delta_ms": 10,
                "num_valid_joints": 17,
                "avg_reproj_error_px": reproj_error,
                "joints_world": {},
                "source_frames": {},
            },
        )

    history = store.read_history(limit=1)
    summary = store.summarize()

    assert len(history) == 1
    assert history[0]["frame_set_id"] == 2
    assert history[0]["avg_reproj_error_px"] == 18.0
    assert summary["run_count"] == 1
    assert summary["runs"][0]["result_count"] == 2
    assert summary["runs"][0]["avg_valid_joints"] == 17.0
    assert summary["runs"][0]["avg_reproj_error_px"] == 15.0
    assert summary["runs"][0]["min_reproj_error_px"] == 12.0
    assert summary["runs"][0]["max_reproj_error_px"] == 18.0
