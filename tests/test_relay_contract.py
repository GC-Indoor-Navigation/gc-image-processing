from app.infrastructure.relay_contract import (
    METHOD_PATH,
    STREAM_FRAME_SETS_METHOD_PATH,
    RelayAck,
    RelayFrame,
    RelayFrameSet,
    RelayFrameSetFrame,
    deserialize_relay_ack,
    deserialize_relay_frame,
    deserialize_relay_frame_set,
    serialize_relay_ack,
    serialize_relay_frame,
    serialize_relay_frame_set,
)


def test_relay_method_path_matches_processing_relay_proto():
    assert (
        METHOD_PATH
        == "/gc_image_stream.processing.v1.FrameRelayService/StreamFrames"
    )


def test_frame_set_method_path_matches_processing_relay_proto():
    assert (
        STREAM_FRAME_SETS_METHOD_PATH
        == "/gc_image_stream.processing.v1.FrameRelayService/StreamFrameSets"
    )


def test_relay_frame_round_trip_preserves_metadata_and_bytes():
    frame = RelayFrame(
        device_id="camera1",
        timestamp_ms=1000,
        sequence=1,
        content_type="image/jpeg",
        image_bytes=b"frame",
        file_path="storage/camera1/1.jpg",
    )

    restored = deserialize_relay_frame(serialize_relay_frame(frame))

    assert restored == frame


def test_relay_frame_set_round_trip_preserves_metadata_and_bytes():
    frame_set = RelayFrameSet(
        frame_set_id=7,
        anchor_timestamp_ms=1000,
        max_delta_ms=12,
        frames=(
            RelayFrameSetFrame(
                device_id="camera1",
                timestamp_ms=1000,
                sequence=1,
                content_type="image/jpeg",
                image_bytes=b"frame-1",
                file_path="storage/camera1/1.jpg",
                frame_id=101,
            ),
            RelayFrameSetFrame(
                device_id="camera2",
                timestamp_ms=1012,
                sequence=2,
                content_type="image/jpeg",
                image_bytes=b"frame-2",
            ),
        ),
    )

    restored = deserialize_relay_frame_set(serialize_relay_frame_set(frame_set))

    assert restored == frame_set


def test_relay_frame_round_trip_preserves_missing_file_path():
    frame = RelayFrame(
        device_id="camera1",
        timestamp_ms=1000,
        sequence=1,
        content_type="image/jpeg",
        image_bytes=b"frame",
        file_path=None,
    )

    restored = deserialize_relay_frame(serialize_relay_frame(frame))

    assert restored == frame


def test_relay_ack_round_trip_preserves_fields():
    ack = RelayAck(success=True, received_count=2, message="ok")

    restored = deserialize_relay_ack(serialize_relay_ack(ack))

    assert restored == ack
