from time import sleep

from app.buffers.frame_buffer import FrameBufferManager
from app.infrastructure.relay_contract import (
    RelayFrame,
    RelayFrameSet,
    RelayFrameSetFrame,
)
from app.models.frame import SynchronizedFrameSet
from app.pipeline.processor import ProcessingResult
from app.pipeline.queue import ProcessingQueue
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

        def process(self, frame_set):
            self.received.append(frame_set)
            return ProcessingResult(
                frame_set_id=frame_set.frame_set_id,
                status="processed_by_test",
                camera_count=len(frame_set.frames),
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
        assert processor.received == [frame_set]
        assert worker.status()["last_result"]["status"] == "processed_by_test"
        assert worker.status()["last_processed_at"] == 2.0
    finally:
        worker.stop()


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
