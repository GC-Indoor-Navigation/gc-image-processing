from pydantic import BaseModel


class CameraStatusResponse(BaseModel):
    device_id: str
    buffered_count: int
    received_count: int
    sequence_gap_count: int
    last_sequence: int | None
    last_timestamp_ms: int | None


class FrameMetadataResponse(BaseModel):
    device_id: str
    timestamp_ms: int
    sequence: int
    content_type: str
    image_size: int
    source_file_path: str | None


class GrpcStatusResponse(BaseModel):
    enabled: bool
    bind: str | None
    running: bool
    last_error: str | None


class BufferStatusResponse(BaseModel):
    camera_count: int
    received_count: int
    buffer_size: int
    cameras: list[CameraStatusResponse]


class ProcessingStatusResponse(BaseModel):
    grpc: GrpcStatusResponse
    buffer: BufferStatusResponse
