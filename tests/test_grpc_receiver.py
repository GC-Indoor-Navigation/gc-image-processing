from app.infrastructure.grpc_receiver import add_frame_relay_servicer
from app.infrastructure.relay_contract import (
    RelayFrame,
    RelayFrameSet,
    RelayFrameSetFrame,
    build_frame_relay_stub,
    build_frame_set_relay_stub,
)


def test_grpc_receiver_accepts_relay_stream():
    import grpc

    from concurrent import futures

    received = []
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=1))
    add_frame_relay_servicer(server, received.append)
    port = server.add_insecure_port("127.0.0.1:0")
    server.start()

    try:
        channel = grpc.insecure_channel(f"127.0.0.1:{port}")
        stub = build_frame_relay_stub(channel)
        frames = [
            RelayFrame("camera1", 1000, 1, "image/jpeg", b"frame-1"),
            RelayFrame("camera1", 1010, 2, "image/jpeg", b"frame-2"),
        ]

        ack = stub(iter(frames), timeout=5)

        assert ack.success is True
        assert ack.received_count == 2
        assert received == frames
    finally:
        server.stop(0)


def test_grpc_receiver_accepts_relay_frame_set_stream():
    import grpc

    from concurrent import futures

    received = []
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=1))
    add_frame_relay_servicer(
        server,
        frame_handler=lambda frame: None,
        frame_set_handler=received.append,
    )
    port = server.add_insecure_port("127.0.0.1:0")
    server.start()

    try:
        channel = grpc.insecure_channel(f"127.0.0.1:{port}")
        stub = build_frame_set_relay_stub(channel)
        frame_sets = [
            RelayFrameSet(
                frame_set_id=1,
                anchor_timestamp_ms=1000,
                max_delta_ms=10,
                frames=(
                    RelayFrameSetFrame(
                        "camera1",
                        1000,
                        1,
                        "image/jpeg",
                        b"frame-1",
                    ),
                    RelayFrameSetFrame(
                        "camera2",
                        1010,
                        1,
                        "image/jpeg",
                        b"frame-2",
                    ),
                ),
            )
        ]

        ack = stub(iter(frame_sets), timeout=5)

        assert ack.success is True
        assert ack.received_count == 1
        assert received == frame_sets
    finally:
        server.stop(0)
