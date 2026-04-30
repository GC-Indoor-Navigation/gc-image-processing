from app.buffers.frame_buffer import FrameBufferManager
from app.models.frame import IncomingFrame
from app.sync.matcher import SyncMatcher


def make_frame(device_id: str, timestamp_ms: int, sequence: int):
    return IncomingFrame(
        device_id=device_id,
        timestamp_ms=timestamp_ms,
        sequence=sequence,
        content_type="image/jpeg",
        image_bytes=b"frame",
    )


def test_sync_matcher_builds_frame_set_when_all_cameras_match():
    buffer_manager = FrameBufferManager(buffer_size=120)
    matcher = SyncMatcher(
        buffer_manager=buffer_manager,
        expected_cameras=["camera1", "camera2", "camera3"],
        window_ms=30,
    )
    buffer_manager.add_frame(make_frame("camera1", 1000, 1))
    buffer_manager.add_frame(make_frame("camera2", 1010, 1))
    anchor = buffer_manager.add_frame(make_frame("camera3", 990, 1))

    frame_set = matcher.try_match(anchor)

    assert frame_set is not None
    assert frame_set.anchor_timestamp_ms == 990
    assert frame_set.max_delta_ms == 20
    assert set(frame_set.frames) == {"camera1", "camera2", "camera3"}


def test_sync_matcher_returns_none_until_all_cameras_are_present():
    buffer_manager = FrameBufferManager(buffer_size=120)
    matcher = SyncMatcher(
        buffer_manager=buffer_manager,
        expected_cameras=["camera1", "camera2"],
        window_ms=30,
    )
    anchor = buffer_manager.add_frame(make_frame("camera1", 1000, 1))

    assert matcher.try_match(anchor) is None


def test_sync_matcher_deduplicates_same_frame_combination():
    buffer_manager = FrameBufferManager(buffer_size=120)
    matcher = SyncMatcher(
        buffer_manager=buffer_manager,
        expected_cameras=["camera1", "camera2"],
        window_ms=30,
    )
    first = buffer_manager.add_frame(make_frame("camera1", 1000, 1))
    second = buffer_manager.add_frame(make_frame("camera2", 1005, 1))

    assert matcher.try_match(second) is not None
    assert matcher.try_match(first) is None


def test_sync_matcher_rejects_frames_outside_window():
    buffer_manager = FrameBufferManager(buffer_size=120)
    matcher = SyncMatcher(
        buffer_manager=buffer_manager,
        expected_cameras=["camera1", "camera2"],
        window_ms=10,
    )
    buffer_manager.add_frame(make_frame("camera1", 1000, 1))
    anchor = buffer_manager.add_frame(make_frame("camera2", 1020, 1))

    assert matcher.try_match(anchor) is None

