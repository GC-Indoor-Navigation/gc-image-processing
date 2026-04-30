from pydantic import BaseModel


class SyncStatusResponse(BaseModel):
    enabled: bool
    expected_cameras: list[str]
    window_ms: int


class QueueStatusResponse(BaseModel):
    queue_size: int
    enqueued_count: int
    dequeued_count: int


class WorkerStatusResponse(BaseModel):
    enabled: bool
    running: bool
    processed_count: int
    error_count: int
    last_processed_frame_set_id: int | None
    last_processed_at: float | None
    last_error: str | None


class PipelineStatusResponse(BaseModel):
    sync: SyncStatusResponse
    queue: QueueStatusResponse
    worker: WorkerStatusResponse
