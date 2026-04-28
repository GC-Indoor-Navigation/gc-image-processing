from fastapi import Request

from app.core.config import Settings
from app.infrastructure.grpc_receiver import GrpcRelayReceiver
from app.services.processing import ProcessingService


def get_processing_service(request: Request) -> ProcessingService:
    return request.app.state.processing_service


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_grpc_receiver(request: Request) -> GrpcRelayReceiver | None:
    return request.app.state.grpc_receiver
