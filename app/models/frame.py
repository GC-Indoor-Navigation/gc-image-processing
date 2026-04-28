from dataclasses import dataclass


@dataclass(frozen=True)
class IncomingFrame:
    device_id: str
    timestamp_ms: int
    sequence: int
    content_type: str
    image_bytes: bytes
    file_path: str | None = None


@dataclass(frozen=True)
class StoredFrame:
    device_id: str
    timestamp_ms: int
    sequence: int
    content_type: str
    image_bytes: bytes
    image_size: int
    source_file_path: str | None


@dataclass(frozen=True)
class CameraStatus:
    device_id: str
    buffered_count: int
    received_count: int
    sequence_gap_count: int
    last_sequence: int | None
    last_timestamp_ms: int | None

