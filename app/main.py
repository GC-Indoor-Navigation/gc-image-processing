import argparse
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI

from app.api.routes.health import router as health_router
from app.api.routes.pipeline import router as pipeline_router
from app.api.routes.status import router as status_router
from app.buffers.frame_buffer import FrameBufferManager
from app.core.config import Settings, load_settings
from app.infrastructure.debug_dump import DebugFrameDumper
from app.infrastructure.grpc_receiver import GrpcRelayReceiver
from app.pipeline.queue import ProcessingQueue
from app.pipeline.processor import MotionCaptureProcessor
from app.pipeline.worker import MotionCaptureWorker
from app.services.processing import ProcessingService
from app.sync.matcher import SyncMatcher


LOGGER = logging.getLogger("app.main")


def create_app(
    settings: Settings | None = None,
    service: ProcessingService | None = None,
) -> FastAPI:
    app_settings = settings or load_settings()
    buffer_manager = FrameBufferManager(buffer_size=app_settings.buffer_size)
    processing_queue = ProcessingQueue()
    sync_matcher = (
        SyncMatcher(
            buffer_manager=buffer_manager,
            expected_cameras=list(app_settings.expected_cameras),
            window_ms=app_settings.sync_window_ms,
        )
        if app_settings.sync_enabled
        else None
    )
    motion_capture_processor = build_motion_capture_processor(app_settings)
    motion_capture_worker = (
        MotionCaptureWorker(
            processing_queue=processing_queue,
            processor=motion_capture_processor,
        )
        if app_settings.worker_enabled
        else None
    )
    processing_service = service or ProcessingService(
        buffer_manager=buffer_manager,
        debug_dumper=DebugFrameDumper(
            enabled=app_settings.debug_dump_enabled,
            dump_dir=app_settings.debug_dump_dir,
            max_per_camera=app_settings.debug_dump_max_per_camera,
        ),
        sync_matcher=sync_matcher,
        processing_queue=processing_queue,
    )
    grpc_receiver = (
        GrpcRelayReceiver(
            bind=app_settings.grpc_bind,
            frame_handler=processing_service.handle_relay_frame,
            frame_set_handler=processing_service.handle_relay_frame_set,
        )
        if app_settings.grpc_enabled
        else None
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if app_settings.mmpose_preload and motion_capture_processor is not None:
            prepare_processor = getattr(motion_capture_processor, "prepare", None)
            if callable(prepare_processor):
                LOGGER.info("preloading motion capture processor")
                prepare_processor()
                LOGGER.info("motion capture processor preloaded")
        if motion_capture_worker is not None:
            motion_capture_worker.start()
        if grpc_receiver is not None:
            grpc_receiver.start()
        try:
            yield
        finally:
            if grpc_receiver is not None:
                grpc_receiver.stop()
            if motion_capture_worker is not None:
                motion_capture_worker.stop()

    fastapi_app = FastAPI(
        title="GC Image Processing Server",
        lifespan=lifespan,
    )
    fastapi_app.state.settings = app_settings
    fastapi_app.state.processing_service = processing_service
    fastapi_app.state.grpc_receiver = grpc_receiver
    fastapi_app.state.processing_queue = processing_queue
    fastapi_app.state.motion_capture_worker = motion_capture_worker
    fastapi_app.state.sync_matcher = sync_matcher
    fastapi_app.include_router(health_router)
    fastapi_app.include_router(status_router)
    fastapi_app.include_router(pipeline_router)
    return fastapi_app


def build_motion_capture_processor(settings: Settings) -> MotionCaptureProcessor | None:
    processor = settings.processor.strip().lower()
    if processor in {"", "placeholder"}:
        return None
    if processor in {"mmpose_triangulation", "mmpose-triangulation"}:
        from app.pipeline.mmpose_triangulation import (
            MMPoseTriangulationConfig,
            MMPoseTriangulationProcessor,
            parse_camera_mapping,
        )

        if settings.mmpose_calib_json is None:
            raise ValueError(
                "PROCESSING_MMPOSE_CALIB_JSON is required for mmpose_triangulation"
            )
        if not settings.mmpose_camera_mapping:
            raise ValueError(
                "PROCESSING_MMPOSE_CAMERA_MAPPING is required for "
                "mmpose_triangulation"
            )

        return MMPoseTriangulationProcessor(
            MMPoseTriangulationConfig(
                calib_json=settings.mmpose_calib_json,
                camera_mapping=parse_camera_mapping(settings.mmpose_camera_mapping),
                pose2d=settings.mmpose_pose2d,
                device=settings.mmpose_device,
                kpt_thr=settings.mmpose_kpt_thr,
                max_reproj_error=settings.mmpose_max_reproj_error,
                images_undistorted=settings.mmpose_images_undistorted,
                extrinsic_source=settings.mmpose_extrinsic_source,
                extrinsic_convention=settings.mmpose_extrinsic_convention,
                temp_dir=settings.mmpose_temp_dir,
            )
        )
    raise ValueError(f"unknown processing processor: {settings.processor}")


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
    parser.add_argument("--sync-enabled", action="store_true")
    parser.add_argument("--sync-window-ms", type=int, default=50)
    parser.add_argument("--expected-camera", action="append", default=[])
    parser.add_argument("--disable-worker", action="store_true")
    parser.add_argument("--processor", default="placeholder")
    parser.add_argument("--mmpose-calib-json", default=None)
    parser.add_argument("--mmpose-camera-mapping", action="append", default=[])
    parser.add_argument("--mmpose-pose2d", default="human")
    parser.add_argument("--mmpose-device", default="cuda:0")
    parser.add_argument("--mmpose-kpt-thr", type=float, default=0.30)
    parser.add_argument("--mmpose-max-reproj-error", type=float, default=40.0)
    parser.add_argument("--mmpose-images-undistorted", action="store_true")
    parser.add_argument("--mmpose-extrinsic-source", default="auto")
    parser.add_argument("--mmpose-extrinsic-convention", default="world_to_camera")
    parser.add_argument("--mmpose-temp-dir", default=None)
    parser.add_argument("--mmpose-preload", action="store_true")
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
        sync_enabled=args.sync_enabled,
        sync_window_ms=args.sync_window_ms,
        expected_cameras=tuple(args.expected_camera),
        worker_enabled=not args.disable_worker,
        processor=args.processor,
        mmpose_calib_json=(
            Path(args.mmpose_calib_json) if args.mmpose_calib_json else None
        ),
        mmpose_camera_mapping=tuple(args.mmpose_camera_mapping),
        mmpose_pose2d=args.mmpose_pose2d,
        mmpose_device=args.mmpose_device,
        mmpose_kpt_thr=args.mmpose_kpt_thr,
        mmpose_max_reproj_error=args.mmpose_max_reproj_error,
        mmpose_images_undistorted=args.mmpose_images_undistorted,
        mmpose_extrinsic_source=args.mmpose_extrinsic_source,
        mmpose_extrinsic_convention=args.mmpose_extrinsic_convention,
        mmpose_temp_dir=Path(args.mmpose_temp_dir) if args.mmpose_temp_dir else None,
        mmpose_preload=args.mmpose_preload,
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
