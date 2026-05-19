from dataclasses import dataclass

from google.protobuf import descriptor_pb2, descriptor_pool, message_factory


PACKAGE_NAME = "gc_image_stream.processing.v1"
SERVICE_NAME = f"{PACKAGE_NAME}.FrameRelayService"
STREAM_FRAMES_METHOD_NAME = "StreamFrames"
STREAM_FRAME_SETS_METHOD_NAME = "StreamFrameSets"
METHOD_NAME = STREAM_FRAMES_METHOD_NAME
METHOD_PATH = f"/{SERVICE_NAME}/{STREAM_FRAMES_METHOD_NAME}"
STREAM_FRAME_SETS_METHOD_PATH = f"/{SERVICE_NAME}/{STREAM_FRAME_SETS_METHOD_NAME}"


def _add_optional_field(message, name: str, number: int, field_type: int):
    field = message.field.add()
    field.name = name
    field.number = number
    field.label = descriptor_pb2.FieldDescriptorProto.LABEL_OPTIONAL
    field.type = field_type
    return field


def _add_proto3_optional_field(message, name: str, number: int, field_type: int):
    oneof = message.oneof_decl.add()
    oneof.name = f"_{name}"
    field = _add_optional_field(message, name, number, field_type)
    field.proto3_optional = True
    field.oneof_index = len(message.oneof_decl) - 1
    return field


def _build_file_descriptor() -> bytes:
    file_proto = descriptor_pb2.FileDescriptorProto()
    file_proto.name = "processing_relay.proto"
    file_proto.package = PACKAGE_NAME
    file_proto.syntax = "proto3"

    relay_frame = file_proto.message_type.add()
    relay_frame.name = "RelayFrame"
    _add_optional_field(
        relay_frame,
        "device_id",
        1,
        descriptor_pb2.FieldDescriptorProto.TYPE_STRING,
    )
    _add_optional_field(
        relay_frame,
        "timestamp_ms",
        2,
        descriptor_pb2.FieldDescriptorProto.TYPE_INT64,
    )
    _add_optional_field(
        relay_frame,
        "sequence",
        3,
        descriptor_pb2.FieldDescriptorProto.TYPE_UINT64,
    )
    _add_optional_field(
        relay_frame,
        "content_type",
        4,
        descriptor_pb2.FieldDescriptorProto.TYPE_STRING,
    )
    _add_optional_field(
        relay_frame,
        "image_bytes",
        5,
        descriptor_pb2.FieldDescriptorProto.TYPE_BYTES,
    )
    _add_proto3_optional_field(
        relay_frame,
        "file_path",
        6,
        descriptor_pb2.FieldDescriptorProto.TYPE_STRING,
    )

    relay_ack = file_proto.message_type.add()
    relay_ack.name = "RelayAck"
    _add_optional_field(
        relay_ack,
        "success",
        1,
        descriptor_pb2.FieldDescriptorProto.TYPE_BOOL,
    )
    _add_optional_field(
        relay_ack,
        "received_count",
        2,
        descriptor_pb2.FieldDescriptorProto.TYPE_UINT64,
    )
    _add_optional_field(
        relay_ack,
        "message",
        3,
        descriptor_pb2.FieldDescriptorProto.TYPE_STRING,
    )

    relay_frame_set = file_proto.message_type.add()
    relay_frame_set.name = "RelayFrameSet"
    _add_optional_field(
        relay_frame_set,
        "frame_set_id",
        1,
        descriptor_pb2.FieldDescriptorProto.TYPE_UINT64,
    )
    _add_optional_field(
        relay_frame_set,
        "anchor_timestamp_ms",
        2,
        descriptor_pb2.FieldDescriptorProto.TYPE_INT64,
    )
    _add_optional_field(
        relay_frame_set,
        "max_delta_ms",
        3,
        descriptor_pb2.FieldDescriptorProto.TYPE_INT64,
    )
    frames_field = relay_frame_set.field.add()
    frames_field.name = "frames"
    frames_field.number = 4
    frames_field.label = descriptor_pb2.FieldDescriptorProto.LABEL_REPEATED
    frames_field.type = descriptor_pb2.FieldDescriptorProto.TYPE_MESSAGE
    frames_field.type_name = f".{PACKAGE_NAME}.RelayFrameSetFrame"

    relay_frame_set_frame = file_proto.message_type.add()
    relay_frame_set_frame.name = "RelayFrameSetFrame"
    _add_optional_field(
        relay_frame_set_frame,
        "device_id",
        1,
        descriptor_pb2.FieldDescriptorProto.TYPE_STRING,
    )
    _add_optional_field(
        relay_frame_set_frame,
        "timestamp_ms",
        2,
        descriptor_pb2.FieldDescriptorProto.TYPE_INT64,
    )
    _add_optional_field(
        relay_frame_set_frame,
        "sequence",
        3,
        descriptor_pb2.FieldDescriptorProto.TYPE_UINT64,
    )
    _add_optional_field(
        relay_frame_set_frame,
        "content_type",
        4,
        descriptor_pb2.FieldDescriptorProto.TYPE_STRING,
    )
    _add_optional_field(
        relay_frame_set_frame,
        "image_bytes",
        5,
        descriptor_pb2.FieldDescriptorProto.TYPE_BYTES,
    )
    _add_proto3_optional_field(
        relay_frame_set_frame,
        "file_path",
        6,
        descriptor_pb2.FieldDescriptorProto.TYPE_STRING,
    )
    _add_proto3_optional_field(
        relay_frame_set_frame,
        "frame_id",
        7,
        descriptor_pb2.FieldDescriptorProto.TYPE_UINT64,
    )

    service = file_proto.service.add()
    service.name = "FrameRelayService"
    stream_frames = service.method.add()
    stream_frames.name = STREAM_FRAMES_METHOD_NAME
    stream_frames.input_type = f".{PACKAGE_NAME}.RelayFrame"
    stream_frames.output_type = f".{PACKAGE_NAME}.RelayAck"
    stream_frames.client_streaming = True

    stream_frame_sets = service.method.add()
    stream_frame_sets.name = STREAM_FRAME_SETS_METHOD_NAME
    stream_frame_sets.input_type = f".{PACKAGE_NAME}.RelayFrameSet"
    stream_frame_sets.output_type = f".{PACKAGE_NAME}.RelayAck"
    stream_frame_sets.client_streaming = True

    return file_proto.SerializeToString()


_POOL = descriptor_pool.DescriptorPool()
_POOL.AddSerializedFile(_build_file_descriptor())
_RelayFrameMessage = message_factory.GetMessageClass(
    _POOL.FindMessageTypeByName(f"{PACKAGE_NAME}.RelayFrame")
)
_RelayAckMessage = message_factory.GetMessageClass(
    _POOL.FindMessageTypeByName(f"{PACKAGE_NAME}.RelayAck")
)
_RelayFrameSetMessage = message_factory.GetMessageClass(
    _POOL.FindMessageTypeByName(f"{PACKAGE_NAME}.RelayFrameSet")
)
_RelayFrameSetFrameMessage = message_factory.GetMessageClass(
    _POOL.FindMessageTypeByName(f"{PACKAGE_NAME}.RelayFrameSetFrame")
)


@dataclass(frozen=True)
class RelayFrame:
    device_id: str
    timestamp_ms: int
    sequence: int
    content_type: str
    image_bytes: bytes
    file_path: str | None = None


@dataclass(frozen=True)
class RelayFrameSetFrame:
    device_id: str
    timestamp_ms: int
    sequence: int
    content_type: str
    image_bytes: bytes
    file_path: str | None = None
    frame_id: int | None = None


@dataclass(frozen=True)
class RelayFrameSet:
    frame_set_id: int
    anchor_timestamp_ms: int
    max_delta_ms: int
    frames: tuple[RelayFrameSetFrame, ...]


@dataclass(frozen=True)
class RelayAck:
    success: bool
    received_count: int
    message: str = ""


def _frame_to_proto(frame: RelayFrame):
    message = _RelayFrameMessage(
        device_id=frame.device_id,
        timestamp_ms=frame.timestamp_ms,
        sequence=frame.sequence,
        content_type=frame.content_type,
        image_bytes=frame.image_bytes,
    )
    if frame.file_path is not None:
        message.file_path = frame.file_path
    return message


def _frame_set_frame_to_proto(frame: RelayFrameSetFrame):
    message = _RelayFrameSetFrameMessage(
        device_id=frame.device_id,
        timestamp_ms=frame.timestamp_ms,
        sequence=frame.sequence,
        content_type=frame.content_type,
        image_bytes=frame.image_bytes,
    )
    if frame.file_path is not None:
        message.file_path = frame.file_path
    if frame.frame_id is not None:
        message.frame_id = frame.frame_id
    return message


def _frame_set_to_proto(frame_set: RelayFrameSet):
    message = _RelayFrameSetMessage(
        frame_set_id=frame_set.frame_set_id,
        anchor_timestamp_ms=frame_set.anchor_timestamp_ms,
        max_delta_ms=frame_set.max_delta_ms,
    )
    message.frames.extend(
        _frame_set_frame_to_proto(frame)
        for frame in frame_set.frames
    )
    return message


def _ack_to_proto(ack: RelayAck):
    return _RelayAckMessage(
        success=ack.success,
        received_count=ack.received_count,
        message=ack.message,
    )


def _proto_to_frame(message) -> RelayFrame:
    file_path = message.file_path if message.HasField("file_path") else None
    return RelayFrame(
        device_id=message.device_id,
        timestamp_ms=message.timestamp_ms,
        sequence=message.sequence,
        content_type=message.content_type,
        image_bytes=message.image_bytes,
        file_path=file_path,
    )


def _proto_to_frame_set_frame(message) -> RelayFrameSetFrame:
    file_path = message.file_path if message.HasField("file_path") else None
    frame_id = int(message.frame_id) if message.HasField("frame_id") else None
    return RelayFrameSetFrame(
        device_id=message.device_id,
        timestamp_ms=message.timestamp_ms,
        sequence=message.sequence,
        content_type=message.content_type,
        image_bytes=message.image_bytes,
        file_path=file_path,
        frame_id=frame_id,
    )


def _proto_to_frame_set(message) -> RelayFrameSet:
    return RelayFrameSet(
        frame_set_id=int(message.frame_set_id),
        anchor_timestamp_ms=message.anchor_timestamp_ms,
        max_delta_ms=message.max_delta_ms,
        frames=tuple(_proto_to_frame_set_frame(frame) for frame in message.frames),
    )


def _proto_to_ack(message) -> RelayAck:
    return RelayAck(
        success=bool(message.success),
        received_count=int(message.received_count),
        message=message.message,
    )


def serialize_relay_frame(frame: RelayFrame) -> bytes:
    return _frame_to_proto(frame).SerializeToString()


def deserialize_relay_frame(payload: bytes) -> RelayFrame:
    message = _RelayFrameMessage()
    message.ParseFromString(payload)
    return _proto_to_frame(message)


def serialize_relay_frame_set(frame_set: RelayFrameSet) -> bytes:
    return _frame_set_to_proto(frame_set).SerializeToString()


def deserialize_relay_frame_set(payload: bytes) -> RelayFrameSet:
    message = _RelayFrameSetMessage()
    message.ParseFromString(payload)
    return _proto_to_frame_set(message)


def serialize_relay_ack(ack: RelayAck) -> bytes:
    return _ack_to_proto(ack).SerializeToString()


def deserialize_relay_ack(payload: bytes) -> RelayAck:
    message = _RelayAckMessage()
    message.ParseFromString(payload)
    return _proto_to_ack(message)


def build_frame_relay_stub(channel):
    return channel.stream_unary(
        METHOD_PATH,
        request_serializer=serialize_relay_frame,
        response_deserializer=deserialize_relay_ack,
    )


def build_frame_set_relay_stub(channel):
    return channel.stream_unary(
        STREAM_FRAME_SETS_METHOD_PATH,
        request_serializer=serialize_relay_frame_set,
        response_deserializer=deserialize_relay_ack,
    )
