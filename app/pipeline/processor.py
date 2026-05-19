from dataclasses import dataclass
from time import time
from typing import Protocol

from app.models.frame import SynchronizedFrameSet


@dataclass(frozen=True)
class ProcessingResult:
    frame_set_id: int
    status: str
    camera_count: int
    started_at: float
    finished_at: float
    elapsed_ms: float


class MotionCaptureProcessor(Protocol):
    def process(self, frame_set: SynchronizedFrameSet) -> ProcessingResult:
        ...


class PlaceholderMotionCaptureProcessor:
    def process(self, frame_set: SynchronizedFrameSet) -> ProcessingResult:
        started_at = time()
        finished_at = time()
        return ProcessingResult(
            frame_set_id=frame_set.frame_set_id,
            status="placeholder_processed",
            camera_count=len(frame_set.frames),
            started_at=started_at,
            finished_at=finished_at,
            elapsed_ms=(finished_at - started_at) * 1000,
        )
