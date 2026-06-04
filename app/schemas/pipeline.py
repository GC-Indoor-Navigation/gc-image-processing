from typing import Any

from pydantic import BaseModel


class SyncStatusResponse(BaseModel):
    enabled: bool
    expected_cameras: list[str]
    window_ms: int
    matched_count: int
    missed_count: int
    duplicate_count: int
    ignored_count: int
    last_frame_set_id: int | None
    last_anchor_timestamp_ms: int | None
    last_max_delta_ms: int | None
    last_missing_cameras: list[str]
    last_reason: str | None


class QueueStatusResponse(BaseModel):
    queue_size: int
    enqueued_count: int
    dequeued_count: int


class RelayFrameSetStatusResponse(BaseModel):
    accepted_count: int
    duplicate_count: int
    last_frame_set_id: int | None
    current_run_id: int
    run_idle_reset_sec: float


class RelayPathStatusResponse(BaseModel):
    primary_method: str
    raw_stream_frames_mode: str
    raw_sync_enabled: bool


class ProcessingResultResponse(BaseModel):
    frame_set_id: int
    status: str
    camera_count: int
    started_at: float
    finished_at: float
    elapsed_ms: float


class WorkerStatusResponse(BaseModel):
    enabled: bool
    running: bool
    processed_count: int
    error_count: int
    last_processed_frame_set_id: int | None
    last_processed_at: float | None
    last_result: ProcessingResultResponse | None
    last_error: str | None


class AlertStatusResponse(BaseModel):
    enabled: bool
    target_configured: bool
    sent_count: int
    failed_count: int
    skipped_count: int
    last_event_id: str | None
    last_error: str | None


class PipelineStatusResponse(BaseModel):
    relay_path: RelayPathStatusResponse
    sync: SyncStatusResponse
    relay_frame_sets: RelayFrameSetStatusResponse
    queue: QueueStatusResponse
    worker: WorkerStatusResponse
    alerts: AlertStatusResponse


class LatestTriangulationResultResponse(BaseModel):
    available: bool
    processor: str | None
    processing_result: ProcessingResultResponse | None
    result: dict[str, Any] | None
    last_error: str | None


class ResultStorageRunResponse(BaseModel):
    path: str
    written_count: int


class ResultStorageStatusResponse(BaseModel):
    enabled: bool
    output_dir: str | None
    last_written_path: str | None
    runs: dict[str, ResultStorageRunResponse]


class ResultHistoryItemResponse(BaseModel):
    written_at: float | None
    relay_run_id: int | None
    frame_set_id: int | None
    status: str | None
    elapsed_ms: float | None
    num_valid_joints: int | None
    avg_reproj_error_px: float | None
    max_delta_ms: int | None
    source_frames: dict[str, Any]


class ResultDetailResponse(BaseModel):
    run_key: str
    path: str
    written_at: float | None
    relay_run_id: int | None
    frame_set_id: int
    processing_result: dict[str, Any] | None
    triangulation_summary: dict[str, Any] | None


class ResultSummaryRunResponse(BaseModel):
    relay_run_id: int
    run_key: str
    path: str
    result_count: int
    first_frame_set_id: int | None
    last_frame_set_id: int | None
    status_counts: dict[str, int]
    avg_valid_joints: float | None
    avg_reproj_error_px: float | None
    min_reproj_error_px: float | None
    max_reproj_error_px: float | None
    avg_elapsed_ms: float | None
    worst_reproj_frame_set_id: int | None
    slowest_frame_set_id: int | None
    max_elapsed_ms: float | None


class ResultSummaryResponse(BaseModel):
    enabled: bool
    output_dir: str | None
    run_count: int
    runs: list[ResultSummaryRunResponse]


class FrameSetFrameResponse(BaseModel):
    device_id: str
    timestamp_ms: int
    sequence: int
    content_type: str
    image_size: int
    source_file_path: str | None
    source_frame_id: int | None = None


class FrameSetResponse(BaseModel):
    frame_set_id: int
    anchor_timestamp_ms: int
    max_delta_ms: int
    frames: dict[str, FrameSetFrameResponse]
