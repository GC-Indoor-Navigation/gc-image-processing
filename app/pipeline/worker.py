import logging
from dataclasses import asdict
from threading import Event, Thread

from app.pipeline.alerts import (
    AlertPublisher,
    NoOpProximityAlertEvaluator,
    ProximityAlertEvaluator,
)
from app.pipeline.input_adapter import MotionCaptureInputAdapter
from app.pipeline.processor import (
    MotionCaptureProcessor,
    PlaceholderMotionCaptureProcessor,
    ProcessingResult,
)
from app.pipeline.queue import ProcessingQueue
from app.pipeline.result_store import JsonlTriangulationResultStore


LOGGER = logging.getLogger("app.pipeline.worker")


class MotionCaptureWorker:
    def __init__(
        self,
        processing_queue: ProcessingQueue,
        processor: MotionCaptureProcessor | None = None,
        input_adapter: MotionCaptureInputAdapter | None = None,
        result_store: JsonlTriangulationResultStore | None = None,
        alert_evaluator: ProximityAlertEvaluator | None = None,
        alert_publisher: AlertPublisher | None = None,
        alert_ttl_ms: int = 500,
    ):
        self.processing_queue = processing_queue
        self.processor = processor or PlaceholderMotionCaptureProcessor()
        self.input_adapter = input_adapter or MotionCaptureInputAdapter()
        self.result_store = result_store
        self.alert_evaluator = alert_evaluator or NoOpProximityAlertEvaluator()
        self.alert_publisher = alert_publisher
        self.alert_ttl_ms = alert_ttl_ms
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
                if self.result_store is not None:
                    self.result_store.save(
                        frame_set=frame_set,
                        processing_result=result,
                        skeleton_result=getattr(
                            self.processor,
                            "last_skeleton_result",
                            None,
                        ),
                    )
                self._handle_alerts(frame_set=frame_set, result=result)
                LOGGER.info(
                    (
                        "processed frame_set_id=%s cameras=%s status=%s "
                        "elapsed_ms=%.2f elapsed_sec=%.3f "
                        "per_camera_frame_ms=%s effective_frame_set_fps=%s"
                    ),
                    frame_set.frame_set_id,
                    sorted(frame_set.frames),
                    result.status,
                    result.elapsed_ms,
                    result.elapsed_ms / 1000,
                    _format_optional_float(
                        _per_camera_frame_ms(result.elapsed_ms, result.camera_count)
                    ),
                    _format_optional_float(_effective_fps(result.elapsed_ms)),
                )
            except Exception as exc:
                self.error_count += 1
                self.last_error = str(exc)
                LOGGER.exception("motion capture worker failed")
            finally:
                self.processing_queue.task_done()

    def _handle_alerts(self, *, frame_set, result: ProcessingResult) -> None:
        if self.alert_publisher is None:
            return
        try:
            alert = self.alert_evaluator.evaluate(
                processing_result=result,
                skeleton_result=getattr(self.processor, "last_skeleton_result", None),
                ttl_ms=self.alert_ttl_ms,
                processor_name=self.processor.__class__.__name__,
                camera_devices=tuple(sorted(frame_set.frames)),
            )
            if alert is not None:
                self.alert_publisher.publish(alert)
        except Exception:
            LOGGER.exception("alert evaluation failed")


def _per_camera_frame_ms(elapsed_ms: float, camera_count: int) -> float | None:
    if camera_count <= 0:
        return None
    return elapsed_ms / camera_count


def _effective_fps(elapsed_ms: float) -> float | None:
    if elapsed_ms <= 0:
        return None
    return 1000 / elapsed_ms


def _format_optional_float(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value:.3f}"
