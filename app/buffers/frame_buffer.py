from collections import deque
from threading import Lock

from app.models.frame import CameraStatus, IncomingFrame, StoredFrame


class CameraBuffer:
    def __init__(self, max_frames: int):
        self.max_frames = max_frames
        self.frames: deque[StoredFrame] = deque(maxlen=max_frames)
        self.received_count = 0
        self.sequence_gap_count = 0
        self.last_sequence: int | None = None
        self.last_timestamp_ms: int | None = None

    def append(self, frame: StoredFrame):
        if self.last_sequence is not None and frame.sequence != self.last_sequence + 1:
            self.sequence_gap_count += 1
        self.frames.append(frame)
        self.received_count += 1
        self.last_sequence = frame.sequence
        self.last_timestamp_ms = frame.timestamp_ms

    def status(self, device_id: str) -> CameraStatus:
        return CameraStatus(
            device_id=device_id,
            buffered_count=len(self.frames),
            received_count=self.received_count,
            sequence_gap_count=self.sequence_gap_count,
            last_sequence=self.last_sequence,
            last_timestamp_ms=self.last_timestamp_ms,
        )

    def latest_frame(self) -> StoredFrame | None:
        if not self.frames:
            return None
        return self.frames[-1]


class FrameBufferManager:
    def __init__(self, buffer_size: int = 120):
        self.buffer_size = buffer_size
        self._buffers: dict[str, CameraBuffer] = {}
        self._received_count = 0
        self._lock = Lock()

    def add_frame(self, frame: IncomingFrame) -> StoredFrame:
        stored = StoredFrame(
            device_id=frame.device_id,
            timestamp_ms=frame.timestamp_ms,
            sequence=frame.sequence,
            content_type=frame.content_type,
            image_bytes=frame.image_bytes,
            image_size=len(frame.image_bytes),
            source_file_path=frame.file_path,
        )

        with self._lock:
            buffer = self._buffers.setdefault(
                frame.device_id,
                CameraBuffer(max_frames=self.buffer_size),
            )
            buffer.append(stored)
            self._received_count += 1
            return stored

    def camera_statuses(self) -> list[CameraStatus]:
        with self._lock:
            return [
                self._buffers[device_id].status(device_id)
                for device_id in sorted(self._buffers)
            ]

    def camera_status(self, device_id: str) -> CameraStatus | None:
        with self._lock:
            buffer = self._buffers.get(device_id)
            if buffer is None:
                return None
            return buffer.status(device_id)

    def latest_frame(self, device_id: str) -> StoredFrame | None:
        with self._lock:
            buffer = self._buffers.get(device_id)
            if buffer is None:
                return None
            return buffer.latest_frame()

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "camera_count": len(self._buffers),
                "received_count": self._received_count,
                "buffer_size": self.buffer_size,
                "cameras": [
                    self._buffers[device_id].status(device_id)
                    for device_id in sorted(self._buffers)
                ],
            }
