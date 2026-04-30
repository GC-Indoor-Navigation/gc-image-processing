import logging
from dataclasses import asdict, dataclass
from threading import Event, Thread
from time import time

from app.pipeline.queue import ProcessingQueue


LOGGER = logging.getLogger("app.pipeline.worker")


@dataclass(frozen=True)
class PlaceholderProcessingResult:
    frame_set_id: int
    status: str
    camera_count: int
    started_at: float
    finished_at: float
    elapsed_ms: float


class MotionCaptureWorker:
    def __init__(self, processing_queue: ProcessingQueue):
        self.processing_queue = processing_queue
        self._stop_event = Event()
        self._thread: Thread | None = None
        self.processed_count = 0
        self.error_count = 0
        self.last_processed_frame_set_id: int | None = None
        self.last_processed_at: float | None = None
        self.last_result: PlaceholderProcessingResult | None = None
        self.last_error: str | None = None

    def start(self):
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = Thread(target=self._run, daemon=True)
        self._thread.start()
        LOGGER.info("motion capture worker started")

    def stop(self, timeout_sec: float = 2.0):
        if self._thread is None:
            return
        self._stop_event.set()
        self._thread.join(timeout=timeout_sec)
        self._thread = None
        LOGGER.info("motion capture worker stopped")

    def status(self):
        return {
            "running": self._thread is not None and self._thread.is_alive(),
            "processed_count": self.processed_count,
            "error_count": self.error_count,
            "last_processed_frame_set_id": self.last_processed_frame_set_id,
            "last_processed_at": self.last_processed_at,
            "last_result": (
                asdict(self.last_result)
                if self.last_result is not None
                else None
            ),
            "last_error": self.last_error,
        }

    def _run(self):
        while not self._stop_event.is_set():
            frame_set = self.processing_queue.get(timeout=0.5)
            if frame_set is None:
                continue
            try:
                # Placeholder for the motion capture algorithm.
                started_at = time()
                finished_at = time()
                result = PlaceholderProcessingResult(
                    frame_set_id=frame_set.frame_set_id,
                    status="placeholder_processed",
                    camera_count=len(frame_set.frames),
                    started_at=started_at,
                    finished_at=finished_at,
                    elapsed_ms=(finished_at - started_at) * 1000,
                )
                self.processed_count += 1
                self.last_processed_frame_set_id = frame_set.frame_set_id
                self.last_processed_at = finished_at
                self.last_result = result
                self.last_error = None
                LOGGER.info(
                    "placeholder processed frame_set_id=%s cameras=%s",
                    frame_set.frame_set_id,
                    sorted(frame_set.frames),
                )
            except Exception as exc:
                self.error_count += 1
                self.last_error = str(exc)
                LOGGER.exception("motion capture worker failed")
            finally:
                self.processing_queue.task_done()
