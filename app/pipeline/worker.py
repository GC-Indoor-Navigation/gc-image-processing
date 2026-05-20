import logging
from dataclasses import asdict
from threading import Event, Thread

from app.pipeline.input_adapter import MotionCaptureInputAdapter
from app.pipeline.processor import (
    MotionCaptureProcessor,
    PlaceholderMotionCaptureProcessor,
    ProcessingResult,
)
from app.pipeline.queue import ProcessingQueue


LOGGER = logging.getLogger("app.pipeline.worker")


class MotionCaptureWorker:
    def __init__(
        self,
        processing_queue: ProcessingQueue,
        processor: MotionCaptureProcessor | None = None,
        input_adapter: MotionCaptureInputAdapter | None = None,
    ):
        self.processing_queue = processing_queue
        self.processor = processor or PlaceholderMotionCaptureProcessor()
        self.input_adapter = input_adapter or MotionCaptureInputAdapter()
        self._stop_event = Event()
        self._thread: Thread | None = None
        self.processed_count = 0
        self.error_count = 0
        self.last_processed_frame_set_id: int | None = None
        self.last_processed_at: float | None = None
        self.last_result: ProcessingResult | None = None
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
                processing_input = self.input_adapter.from_frame_set(frame_set)
                result = self.processor.process(processing_input)
                self.processed_count += 1
                self.last_processed_frame_set_id = frame_set.frame_set_id
                self.last_processed_at = result.finished_at
                self.last_result = result
                self.last_error = None
                LOGGER.info(
                    "processed frame_set_id=%s cameras=%s status=%s",
                    frame_set.frame_set_id,
                    sorted(frame_set.frames),
                    result.status,
                )
            except Exception as exc:
                self.error_count += 1
                self.last_error = str(exc)
                LOGGER.exception("motion capture worker failed")
            finally:
                self.processing_queue.task_done()
