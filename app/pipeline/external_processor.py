from collections.abc import Callable, Mapping
from time import time
from typing import Any

from app.pipeline.input_adapter import MotionCaptureInput
from app.pipeline.processor import ProcessingResult


ExternalProcessorOutput = ProcessingResult | Mapping[str, Any] | None
ExternalProcessorRunner = Callable[[MotionCaptureInput], ExternalProcessorOutput]


class ExternalMotionCaptureProcessor:
    def __init__(
        self,
        runner: ExternalProcessorRunner,
        default_status: str = "external_processed",
    ):
        self.runner = runner
        self.default_status = default_status

    def process(self, processing_input: MotionCaptureInput) -> ProcessingResult:
        started_at = time()
        raw_result = self.runner(processing_input)
        finished_at = time()

        if isinstance(raw_result, ProcessingResult):
            return raw_result

        return ProcessingResult(
            frame_set_id=processing_input.frame_set_id,
            status=self._status_from(raw_result),
            camera_count=len(processing_input.frames),
            started_at=started_at,
            finished_at=finished_at,
            elapsed_ms=(finished_at - started_at) * 1000,
        )

    def _status_from(self, raw_result: ExternalProcessorOutput) -> str:
        if isinstance(raw_result, Mapping):
            status = raw_result.get("status")
            if isinstance(status, str) and status:
                return status
        return self.default_status
