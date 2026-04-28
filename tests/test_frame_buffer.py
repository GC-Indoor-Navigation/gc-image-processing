from app.buffers.frame_buffer import FrameBufferManager
from app.models.frame import IncomingFrame


def make_frame(device_id: str, timestamp_ms: int, sequence: int):
    return IncomingFrame(
        device_id=device_id,
        timestamp_ms=timestamp_ms,
        sequence=sequence,
        content_type="image/jpeg",
        image_bytes=b"frame",
    )


def test_frame_buffer_tracks_camera_status():
    manager = FrameBufferManager(buffer_size=120)

    manager.add_frame(make_frame("camera1", 1000, 1))

    status = manager.camera_statuses()[0]
    assert status.device_id == "camera1"
    assert status.buffered_count == 1
    assert status.received_count == 1
    assert status.sequence_gap_count == 0


def test_frame_buffer_tracks_sequence_gaps():
    manager = FrameBufferManager(buffer_size=120)

    manager.add_frame(make_frame("camera1", 1000, 1))
    manager.add_frame(make_frame("camera1", 1010, 3))

    status = manager.camera_statuses()[0]
    assert status.sequence_gap_count == 1
    assert status.last_sequence == 3


def test_frame_buffer_respects_max_size():
    manager = FrameBufferManager(buffer_size=1)

    manager.add_frame(make_frame("camera1", 1000, 1))
    manager.add_frame(make_frame("camera1", 1010, 2))

    status = manager.camera_statuses()[0]
    assert status.buffered_count == 1
    assert status.received_count == 2


def test_frame_buffer_returns_latest_frame():
    manager = FrameBufferManager(buffer_size=120)

    manager.add_frame(make_frame("camera1", 1000, 1))
    manager.add_frame(make_frame("camera1", 1010, 2))

    latest = manager.latest_frame("camera1")
    assert latest is not None
    assert latest.sequence == 2


def test_frame_buffer_returns_none_for_missing_latest_frame():
    manager = FrameBufferManager(buffer_size=120)

    assert manager.latest_frame("missing") is None
