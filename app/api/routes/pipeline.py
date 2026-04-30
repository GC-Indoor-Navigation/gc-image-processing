from fastapi import APIRouter, Depends

from app.api.deps import (
    get_motion_capture_worker,
    get_processing_queue,
    get_settings,
    get_sync_matcher,
)
from app.core.config import Settings
from app.pipeline.queue import ProcessingQueue
from app.pipeline.worker import MotionCaptureWorker
from app.schemas.pipeline import FrameSetResponse, PipelineStatusResponse
from app.sync.matcher import SyncMatcher


router = APIRouter(tags=["pipeline"])


def _empty_sync_status():
    return {
        "matched_count": 0,
        "missed_count": 0,
        "duplicate_count": 0,
        "ignored_count": 0,
        "last_frame_set_id": None,
        "last_anchor_timestamp_ms": None,
        "last_max_delta_ms": None,
        "last_missing_cameras": [],
        "last_reason": None,
    }


@router.get("/pipeline/status", response_model=PipelineStatusResponse)
def pipeline_status(
    settings: Settings = Depends(get_settings),
    processing_queue: ProcessingQueue | None = Depends(get_processing_queue),
    worker: MotionCaptureWorker | None = Depends(get_motion_capture_worker),
    sync_matcher: SyncMatcher | None = Depends(get_sync_matcher),
):
    sync_status = (
        sync_matcher.status()
        if sync_matcher is not None
        else _empty_sync_status()
    )
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
            **sync_status,
        },
        "queue": queue_status,
        "worker": {
            "enabled": settings.worker_enabled,
            **worker_status,
        },
    }


@router.get("/pipeline/recent-frame-sets", response_model=list[FrameSetResponse])
def recent_frame_sets(
    sync_matcher: SyncMatcher | None = Depends(get_sync_matcher),
):
    if sync_matcher is None:
        return []
    return [
        {
            "frame_set_id": frame_set.frame_set_id,
            "anchor_timestamp_ms": frame_set.anchor_timestamp_ms,
            "max_delta_ms": frame_set.max_delta_ms,
            "frames": {
                device_id: {
                    "device_id": frame.device_id,
                    "timestamp_ms": frame.timestamp_ms,
                    "sequence": frame.sequence,
                    "content_type": frame.content_type,
                    "image_size": frame.image_size,
                    "source_file_path": frame.source_file_path,
                }
                for device_id, frame in frame_set.frames.items()
            },
        }
        for frame_set in sync_matcher.recent_frame_sets()
    ]
