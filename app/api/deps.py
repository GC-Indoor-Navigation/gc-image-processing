from fastapi import Request

from app.core.config import Settings
from app.infrastructure.grpc_receiver import GrpcRelayReceiver
from app.pipeline.queue import ProcessingQueue
from app.pipeline.worker import MotionCaptureWorker
from app.services.processing import ProcessingService


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
