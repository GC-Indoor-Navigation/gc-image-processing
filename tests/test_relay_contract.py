from app.infrastructure.relay_contract import (
    METHOD_PATH,
    RelayAck,
    RelayFrame,
    deserialize_relay_ack,
    deserialize_relay_frame,
    serialize_relay_ack,
    serialize_relay_frame,
)


def test_relay_method_path_matches_processing_relay_proto():
    assert (
        METHOD_PATH
        == "/gc_image_stream.processing.v1.FrameRelayService/StreamFrames"
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
