import logging
from threading import Event, Thread
from time import time

from app.pipeline.queue import ProcessingQueue


LOGGER = logging.getLogger("app.pipeline.worker")


class MotionCaptureWorker:
    def __init__(self, processing_queue: ProcessingQueue):
        self.processing_queue = processing_queue
        self._stop_event = Event()
        self._thread: Thread | None = None
        self.processed_count = 0
        self.error_count = 0
        self.last_processed_frame_set_id: int | None = None
        self.last_processed_at: float | None = None
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
            "last_error": self.last_error,
        }

    def _run(self):
        while not self._stop_event.is_set():
            frame_set = self.processing_queue.get(timeout=0.5)
            if frame_set is None:
                continue
            try:
                # Placeholder for the motion capture algorithm.
                self.processed_count += 1
                self.last_processed_frame_set_id = frame_set.frame_set_id
                self.last_processed_at = time()
                self.last_error = None
                LOGGER.info(
                    "processed frame_set_id=%s cameras=%s",
                    frame_set.frame_set_id,
                    sorted(frame_set.frames),
                )
            except Exception as exc:
                self.error_count += 1
                self.last_error = str(exc)
                LOGGER.exception("motion capture worker failed")
            finally:
                self.processing_queue.task_done()
