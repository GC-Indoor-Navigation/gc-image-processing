from fastapi import Request

from app.core.config import Settings
from app.infrastructure.grpc_receiver import GrpcRelayReceiver
from app.pipeline.queue import ProcessingQueue
from app.pipeline.result_store import JsonlTriangulationResultStore
from app.pipeline.worker import MotionCaptureWorker
from app.services.processing import ProcessingService
from app.sync.matcher import SyncMatcher


def get_processing_service(request: Request) -> ProcessingService:
    return request.app.state.processing_service


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_grpc_receiver(request: Request) -> GrpcRelayReceiver | None:
    return request.app.state.grpc_receiver


def get_processing_queue(request: Request) -> ProcessingQueue | None:
    return request.app.state.processing_queue


def get_motion_capture_worker(request: Request) -> MotionCaptureWorker | None:
    return request.app.state.motion_capture_worker


def get_result_store(request: Request) -> JsonlTriangulationResultStore | None:
    return request.app.state.result_store


def get_sync_matcher(request: Request) -> SyncMatcher | None:
    return request.app.state.sync_matcher
