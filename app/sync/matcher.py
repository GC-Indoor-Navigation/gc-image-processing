from app.buffers.frame_buffer import FrameBufferManager
from app.models.frame import StoredFrame, SynchronizedFrameSet


class SyncMatcher:
    def __init__(
        self,
        buffer_manager: FrameBufferManager,
        expected_cameras: list[str],
        window_ms: int,
    ):
        self.buffer_manager = buffer_manager
        self.expected_cameras = list(expected_cameras)
        self.window_ms = window_ms
        self._next_frame_set_id = 1
        self._emitted_keys: set[tuple[tuple[str, int], ...]] = set()

    def try_match(self, anchor_frame: StoredFrame) -> SynchronizedFrameSet | None:
        if not self.expected_cameras:
            return None
        if anchor_frame.device_id not in self.expected_cameras:
            return None

        selected: dict[str, StoredFrame] = {}
        for device_id in self.expected_cameras:
            frame = self.buffer_manager.nearest_frame(
                device_id=device_id,
                anchor_timestamp_ms=anchor_frame.timestamp_ms,
                window_ms=self.window_ms,
            )
            if frame is None:
                return None
            selected[device_id] = frame

        key = tuple(
            sorted((device_id, frame.sequence) for device_id, frame in selected.items())
        )
        if key in self._emitted_keys:
            return None
        self._emitted_keys.add(key)

        max_delta_ms = max(
            abs(frame.timestamp_ms - anchor_frame.timestamp_ms)
            for frame in selected.values()
        )
        frame_set = SynchronizedFrameSet(
            frame_set_id=self._next_frame_set_id,
            anchor_timestamp_ms=anchor_frame.timestamp_ms,
            max_delta_ms=max_delta_ms,
            frames=selected,
        )
        self._next_frame_set_id += 1
        return frame_set
