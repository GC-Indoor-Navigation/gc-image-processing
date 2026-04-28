from fastapi import APIRouter, Depends, HTTPException, Response

from app.api.deps import get_grpc_receiver, get_processing_service, get_settings
from app.core.config import Settings
from app.infrastructure.grpc_receiver import GrpcRelayReceiver
from app.schemas.status import (
    CameraStatusResponse,
    FrameMetadataResponse,
    ProcessingStatusResponse,
)
from app.services.processing import ProcessingService


router = APIRouter(tags=["status"])


@router.get("/status", response_model=ProcessingStatusResponse)
def status(
    service: ProcessingService = Depends(get_processing_service),
    settings: Settings = Depends(get_settings),
    grpc_receiver: GrpcRelayReceiver | None = Depends(get_grpc_receiver),
):
    receiver_status = (
        grpc_receiver.status()
        if grpc_receiver is not None
        else {"bind": None, "running": False, "last_error": None}
    )
    return {
        "grpc": {
            "enabled": settings.grpc_enabled,
            **receiver_status,
        },
        "buffer": service.status(),
    }


@router.get("/cameras", response_model=list[CameraStatusResponse])
def cameras(service: ProcessingService = Depends(get_processing_service)):
    return service.camera_statuses()


@router.get("/cameras/{device_id}", response_model=CameraStatusResponse)
def camera(
    device_id: str,
    service: ProcessingService = Depends(get_processing_service),
):
    camera_status = service.camera_status(device_id)
    if camera_status is None:
        raise HTTPException(
            status_code=404,
            detail=f"camera not found: {device_id}",
        )
    return camera_status


@router.get("/cameras/{device_id}/latest", response_model=FrameMetadataResponse)
def latest_frame(
    device_id: str,
    service: ProcessingService = Depends(get_processing_service),
):
    frame = service.latest_frame(device_id)
    if frame is None:
        raise HTTPException(
            status_code=404,
            detail=f"latest frame not found: {device_id}",
        )
    return frame


@router.get("/cameras/{device_id}/latest/image")
def latest_frame_image(
    device_id: str,
    service: ProcessingService = Depends(get_processing_service),
):
    frame = service.latest_frame(device_id)
    if frame is None:
        raise HTTPException(
            status_code=404,
            detail=f"latest frame not found: {device_id}",
        )
    return Response(
        content=frame.image_bytes,
        media_type=frame.content_type,
    )
