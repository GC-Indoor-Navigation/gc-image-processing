import json
from dataclasses import dataclass


SERVICE_NAME = "gc_image_stream.FrameRelayService"
METHOD_NAME = "StreamFrames"
METHOD_PATH = f"/{SERVICE_NAME}/{METHOD_NAME}"


@dataclass(frozen=True)
class RelayFrame:
    device_id: str
    timestamp_ms: int
    sequence: int
    content_type: str
    image_bytes: bytes
    file_path: str | None = None


@dataclass(frozen=True)
class RelayAck:
    success: bool
    received_count: int
    message: str = ""


def serialize_relay_frame(frame: RelayFrame) -> bytes:
    metadata = {
        "device_id": frame.device_id,
        "timestamp_ms": frame.timestamp_ms,
        "sequence": frame.sequence,
        "content_type": frame.content_type,
        "file_path": frame.file_path,
    }
    metadata_bytes = json.dumps(metadata, separators=(",", ":")).encode("utf-8")
    return len(metadata_bytes).to_bytes(8, "big") + metadata_bytes + frame.image_bytes


def deserialize_relay_frame(payload: bytes) -> RelayFrame:
    if len(payload) < 8:
        raise ValueError("relay frame payload is shorter than metadata prefix")

    metadata_length = int.from_bytes(payload[:8], "big")
    metadata_start = 8
    metadata_end = metadata_start + metadata_length
    if len(payload) < metadata_end:
        raise ValueError("relay frame payload is shorter than metadata length")

    metadata = json.loads(payload[metadata_start:metadata_end].decode("utf-8"))
    return RelayFrame(
        device_id=metadata["device_id"],
        timestamp_ms=int(metadata["timestamp_ms"]),
        sequence=int(metadata["sequence"]),
        content_type=metadata["content_type"],
        image_bytes=payload[metadata_end:],
        file_path=metadata.get("file_path"),
    )


def serialize_relay_ack(ack: RelayAck) -> bytes:
    return json.dumps(
        {
            "success": ack.success,
            "received_count": ack.received_count,
            "message": ack.message,
        },
        separators=(",", ":"),
    ).encode("utf-8")


def deserialize_relay_ack(payload: bytes) -> RelayAck:
    data = json.loads(payload.decode("utf-8"))
    return RelayAck(
        success=bool(data["success"]),
        received_count=int(data["received_count"]),
        message=data.get("message", ""),
    )


def build_frame_relay_stub(channel):
    return channel.stream_unary(
        METHOD_PATH,
        request_serializer=serialize_relay_frame,
        response_deserializer=deserialize_relay_ack,
    )

