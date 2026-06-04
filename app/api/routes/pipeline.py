from dataclasses import asdict, is_dataclass
from typing import Any

from fastapi import APIRouter, Depends
from fastapi import HTTPException
from fastapi import Query

from app.api.deps import (
    get_motion_capture_worker,
    get_processing_queue,
    get_processing_service,
    get_result_store,
    get_settings,
    get_sync_matcher,
    get_alert_publisher,
)
from app.core.config import Settings
from app.pipeline.alerts import AlertPublisher
from app.pipeline.queue import ProcessingQueue
from app.pipeline.result_store import JsonlTriangulationResultStore
from app.pipeline.worker import MotionCaptureWorker
from app.schemas.pipeline import (
    FrameSetResponse,
    AlertStatusResponse,
    LatestTriangulationResultResponse,
    PipelineStatusResponse,
    ResultDetailResponse,
    ResultHistoryItemResponse,
    ResultSummaryResponse,
    ResultStorageStatusResponse,
)
from app.services.processing import ProcessingService
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
    service: ProcessingService = Depends(get_processing_service),
    processing_queue: ProcessingQueue | None = Depends(get_processing_queue),
    worker: MotionCaptureWorker | None = Depends(get_motion_capture_worker),
    sync_matcher: SyncMatcher | None = Depends(get_sync_matcher),
    alert_publisher: AlertPublisher | None = Depends(get_alert_publisher),
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
            "last_result": None,
            "last_error": None,
        }
    )
    return {
        "relay_path": {
            "primary_method": "StreamFrameSets",
            "raw_stream_frames_mode": "legacy_fallback",
            "raw_sync_enabled": settings.sync_enabled,
        },
        "sync": {
            "enabled": settings.sync_enabled,
            "expected_cameras": list(settings.expected_cameras),
            "window_ms": settings.sync_window_ms,
            **sync_status,
        },
        "relay_frame_sets": service.relay_frame_set_status(),
        "queue": queue_status,
        "worker": {
            "enabled": settings.worker_enabled,
            **worker_status,
        },
        "alerts": _alert_status(alert_publisher),
    }


@router.get(
    "/pipeline/results/storage",
    response_model=ResultStorageStatusResponse,
)
def result_storage_status(
    result_store: JsonlTriangulationResultStore | None = Depends(get_result_store),
):
    if result_store is None:
        return {
            "enabled": False,
            "output_dir": None,
            "last_written_path": None,
            "runs": {},
        }
    return result_store.status()


@router.get(
    "/pipeline/results/history",
    response_model=list[ResultHistoryItemResponse],
)
def result_history(
    limit: int = Query(20, ge=1, le=500),
    run_key: str | None = Query(None),
    result_store: JsonlTriangulationResultStore | None = Depends(get_result_store),
):
    if result_store is None:
        return []
    return result_store.read_history(limit=limit, run_key=run_key)


@router.get(
    "/pipeline/results/detail",
    response_model=ResultDetailResponse,
)
def result_detail(
    frame_set_id: int = Query(..., ge=0),
    run_key: str | None = Query(None),
    result_store: JsonlTriangulationResultStore | None = Depends(get_result_store),
):
    if result_store is None:
        raise HTTPException(status_code=404, detail="result storage is disabled")
    detail = result_store.read_detail(frame_set_id=frame_set_id, run_key=run_key)
    if detail is None:
        run_context = f" run_key={run_key}" if run_key is not None else ""
        raise HTTPException(
            status_code=404,
            detail=f"result not found for frame_set_id={frame_set_id}{run_context}",
        )
    return detail


@router.get(
    "/pipeline/results/summary",
    response_model=ResultSummaryResponse,
)
def result_summary(
    result_store: JsonlTriangulationResultStore | None = Depends(get_result_store),
):
    if result_store is None:
        return {
            "enabled": False,
            "output_dir": None,
            "run_count": 0,
            "runs": [],
        }
    return result_store.summarize()


@router.get(
    "/pipeline/results/latest",
    response_model=LatestTriangulationResultResponse,
)
def latest_triangulation_result(
    worker: MotionCaptureWorker | None = Depends(get_motion_capture_worker),
):
    if worker is None:
        return {
            "available": False,
            "processor": None,
            "processing_result": None,
            "result": None,
            "last_error": None,
        }

    processor = worker.processor
    skeleton_result = getattr(processor, "last_skeleton_result", None)
    result = _serialize_result(skeleton_result)
    return {
        "available": result is not None,
        "processor": processor.__class__.__name__,
        "processing_result": (
            asdict(worker.last_result)
            if worker.last_result is not None
            else None
        ),
        "result": result,
        "last_error": worker.last_error,
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
                    "source_frame_id": frame.source_frame_id,
                }
                for device_id, frame in frame_set.frames.items()
            },
        }
        for frame_set in sync_matcher.recent_frame_sets()
    ]


def _serialize_result(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, dict):
        return value
    return None


def _alert_status(alert_publisher: AlertPublisher | None) -> dict:
    if alert_publisher is None:
        return {
            "enabled": False,
            "target_configured": False,
            "sent_count": 0,
            "failed_count": 0,
            "skipped_count": 0,
            "last_event_id": None,
            "last_error": None,
        }
    return alert_publisher.status()
