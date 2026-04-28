import argparse
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI

from app.api.routes.health import router as health_router
from app.api.routes.status import router as status_router
from app.buffers.frame_buffer import FrameBufferManager
from app.core.config import Settings, load_settings
from app.infrastructure.debug_dump import DebugFrameDumper
from app.infrastructure.grpc_receiver import GrpcRelayReceiver
from app.services.processing import ProcessingService


LOGGER = logging.getLogger("app.main")


def create_app(
    settings: Settings | None = None,
    service: ProcessingService | None = None,
) -> FastAPI:
    app_settings = settings or load_settings()
    processing_service = service or ProcessingService(
        buffer_manager=FrameBufferManager(buffer_size=app_settings.buffer_size),
        debug_dumper=DebugFrameDumper(
            enabled=app_settings.debug_dump_enabled,
            dump_dir=app_settings.debug_dump_dir,
            max_per_camera=app_settings.debug_dump_max_per_camera,
        ),
    )
    grpc_receiver = (
        GrpcRelayReceiver(
            bind=app_settings.grpc_bind,
            frame_handler=processing_service.handle_relay_frame,
        )
        if app_settings.grpc_enabled
        else None
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if grpc_receiver is not None:
            grpc_receiver.start()
        try:
            yield
        finally:
            if grpc_receiver is not None:
                grpc_receiver.stop()

    fastapi_app = FastAPI(
        title="GC Image Processing Server",
        lifespan=lifespan,
    )
    fastapi_app.state.settings = app_settings
    fastapi_app.state.processing_service = processing_service
    fastapi_app.state.grpc_receiver = grpc_receiver
    fastapi_app.include_router(health_router)
    fastapi_app.include_router(status_router)
    return fastapi_app


def parse_args():
    parser = argparse.ArgumentParser(description="GC image processing FastAPI server")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=9000)
    parser.add_argument("--grpc-bind", default="127.0.0.1:50051")
    parser.add_argument("--disable-grpc", action="store_true")
    parser.add_argument("--buffer-size", type=int, default=120)
    parser.add_argument("--debug-dump-enabled", action="store_true")
    parser.add_argument("--debug-dump-dir", default="debug_frames")
    parser.add_argument("--debug-dump-max-per-camera", type=int, default=20)
    return parser.parse_args()


app = create_app()


def main():
    import uvicorn

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    args = parse_args()
    settings = Settings(
        http_host=args.host,
        http_port=args.port,
        grpc_bind=args.grpc_bind,
        grpc_enabled=not args.disable_grpc,
        buffer_size=args.buffer_size,
        debug_dump_enabled=args.debug_dump_enabled,
        debug_dump_dir=Path(args.debug_dump_dir),
        debug_dump_max_per_camera=args.debug_dump_max_per_camera,
    )
    server_app = create_app(settings=settings)
    LOGGER.info(
        "processing server starting host=%s port=%s grpc_bind=%s grpc_enabled=%s",
        settings.http_host,
        settings.http_port,
        settings.grpc_bind,
        settings.grpc_enabled,
    )
    uvicorn.run(server_app, host=settings.http_host, port=settings.http_port)


if __name__ == "__main__":
    main()
