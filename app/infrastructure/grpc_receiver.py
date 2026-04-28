import logging
from collections.abc import Callable
from concurrent import futures

import grpc

from app.infrastructure.relay_contract import (
    METHOD_NAME,
    SERVICE_NAME,
    RelayAck,
    RelayFrame,
    deserialize_relay_frame,
    serialize_relay_ack,
)


LOGGER = logging.getLogger("app.infrastructure.grpc_receiver")
FrameHandler = Callable[[RelayFrame], None]


class GrpcRelayReceiver:
    def __init__(
        self,
        bind: str,
        frame_handler: FrameHandler,
        max_workers: int = 4,
    ):
        self.bind = bind
        self.frame_handler = frame_handler
        self.max_workers = max_workers
        self._server = None
        self.last_error: str | None = None

    def start(self):
        if self._server is not None:
            return
        try:
            server = create_grpc_server(
                frame_handler=self.frame_handler,
                max_workers=self.max_workers,
            )
            port = server.add_insecure_port(self.bind)
            if port == 0:
                raise RuntimeError(f"failed to bind gRPC receiver to {self.bind}")
            server.start()
            self._server = server
            self.last_error = None
            LOGGER.info("gRPC relay receiver listening on %s", self.bind)
        except Exception as exc:
            self.last_error = str(exc)
            raise

    def stop(self, grace: float = 2.0):
        if self._server is None:
            return
        self._server.stop(grace=grace).wait()
        self._server = None
        LOGGER.info("gRPC relay receiver stopped")

    def status(self):
        return {
            "bind": self.bind,
            "running": self._server is not None,
            "last_error": self.last_error,
        }


def create_grpc_server(frame_handler: FrameHandler, max_workers: int = 4):
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=max_workers))
    add_frame_relay_servicer(server, frame_handler)
    return server


def add_frame_relay_servicer(server, frame_handler: FrameHandler):
    def stream_frames(request_iterator, context):
        received_count = 0

        try:
            for frame in request_iterator:
                received_count += 1
                frame_handler(frame)
        except Exception as exc:
            LOGGER.exception("frame relay stream failed")
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details(str(exc))
            return RelayAck(
                success=False,
                received_count=received_count,
                message=str(exc),
            )

        return RelayAck(
            success=True,
            received_count=received_count,
            message="relay stream completed",
        )

    generic_handler = grpc.method_handlers_generic_handler(
        SERVICE_NAME,
        {
            METHOD_NAME: grpc.stream_unary_rpc_method_handler(
                stream_frames,
                request_deserializer=deserialize_relay_frame,
                response_serializer=serialize_relay_ack,
            )
        },
    )
    server.add_generic_rpc_handlers((generic_handler,))
