from dataclasses import dataclass

from app.models.frame import SynchronizedFrameSet


@dataclass(frozen=True)
class CameraFrameInput:
    device_id: str
    timestamp_ms: int
    sequence: int
    content_type: str
    image_bytes: bytes
    image_size: int
    source_file_path: str | None
    source_frame_id: int | None


@dataclass(frozen=True)
class MotionCaptureInput:
    frame_set_id: int
    anchor_timestamp_ms: int
    max_delta_ms: int
    frames: dict[str, CameraFrameInput]
    relay_run_id: int | None = None


class MotionCaptureInputAdapter:
    def from_frame_set(self, frame_set: SynchronizedFrameSet) -> MotionCaptureInput:
        return MotionCaptureInput(
            frame_set_id=frame_set.frame_set_id,
            anchor_timestamp_ms=frame_set.anchor_timestamp_ms,
            max_delta_ms=frame_set.max_delta_ms,
            relay_run_id=frame_set.relay_run_id,
            frames={
                device_id: CameraFrameInput(
                    device_id=frame.device_id,
                    timestamp_ms=frame.timestamp_ms,
                    sequence=frame.sequence,
                    content_type=frame.content_type,
                    image_bytes=frame.image_bytes,
                    image_size=frame.image_size,
                    source_file_path=frame.source_file_path,
                    source_frame_id=frame.source_frame_id,
                )
                for device_id, frame in frame_set.frames.items()
            },
        )
