from fastapi import APIRouter, Depends

from app.api.deps import (
    get_motion_capture_worker,
    get_processing_queue,
    get_settings,
)
from app.core.config import Settings
from app.pipeline.queue import ProcessingQueue
from app.pipeline.worker import MotionCaptureWorker
from app.schemas.pipeline import PipelineStatusResponse


router = APIRouter(tags=["pipeline"])


@router.get("/pipeline/status", response_model=PipelineStatusResponse)
def pipeline_status(
    settings: Settings = Depends(get_settings),
    processing_queue: ProcessingQueue | None = Depends(get_processing_queue),
    worker: MotionCaptureWorker | None = Depends(get_motion_capture_worker),
):
    queue_status = (
        processing_queue.status()
        if processing_queue is not None
        else {"queue_size": 0, "enqueued_count": 0, "dequeued_count": 0}
    )
    worker_status = (
        worker.status()
        if worker is not None
        else {
            "running": False,
            "processed_count": 0,
            "error_count": 0,
            "last_processed_frame_set_id": None,
            "last_processed_at": None,
            "last_error": None,
        }
    )
    return {
        "sync": {
            "enabled": settings.sync_enabled,
            "expected_cameras": list(settings.expected_cameras),
            "window_ms": settings.sync_window_ms,
        },
        "queue": queue_status,
        "worker": {
            "enabled": settings.worker_enabled,
            **worker_status,
        },
    }
