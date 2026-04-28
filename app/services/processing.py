import logging

from app.buffers.frame_buffer import FrameBufferManager
from app.infrastructure.debug_dump import DebugFrameDumper
from app.infrastructure.relay_contract import RelayFrame
from app.models.frame import IncomingFrame, StoredFrame


LOGGER = logging.getLogger("app.services.processing")


class ProcessingService:
    def __init__(
        self,
        buffer_manager: FrameBufferManager,
        debug_dumper: DebugFrameDumper | None = None,
    ):
        self.buffer_manager = buffer_manager
        self.debug_dumper = debug_dumper

    def handle_relay_frame(self, frame: RelayFrame) -> StoredFrame:
        stored = self.buffer_manager.add_frame(
            IncomingFrame(
                device_id=frame.device_id,
                timestamp_ms=frame.timestamp_ms,
                sequence=frame.sequence,
                content_type=frame.content_type,
                image_bytes=frame.image_bytes,
                file_path=frame.file_path,
            )
        )
        LOGGER.info(
            "buffered frame device_id=%s sequence=%s timestamp_ms=%s size=%s",
            stored.device_id,
            stored.sequence,
            stored.timestamp_ms,
            stored.image_size,
        )
        if self.debug_dumper is not None:
            self.debug_dumper.dump(stored)
        return stored

    def status(self) -> dict:
        return self.buffer_manager.snapshot()

    def camera_statuses(self):
        return self.buffer_manager.camera_statuses()

    def camera_status(self, device_id: str):
        return self.buffer_manager.camera_status(device_id)

    def latest_frame(self, device_id: str):
        return self.buffer_manager.latest_frame(device_id)
