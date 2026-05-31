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


class PipelineStatusResponse(BaseModel):
    relay_path: RelayPathStatusResponse
    sync: SyncStatusResponse
    relay_frame_sets: RelayFrameSetStatusResponse
    queue: QueueStatusResponse
    worker: WorkerStatusResponse


class LatestTriangulationResultResponse(BaseModel):
    available: bool
    processor: str | None
    processing_result: ProcessingResultResponse | None
    result: dict[str, Any] | None
    last_error: str | None


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
