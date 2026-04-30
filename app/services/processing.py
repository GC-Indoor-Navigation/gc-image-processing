import logging

from app.buffers.frame_buffer import FrameBufferManager
from app.infrastructure.debug_dump import DebugFrameDumper
from app.infrastructure.relay_contract import RelayFrame
from app.models.frame import IncomingFrame, StoredFrame
from app.pipeline.queue import ProcessingQueue
from app.sync.matcher import SyncMatcher


LOGGER = logging.getLogger("app.services.processing")


class ProcessingService:
    def __init__(
        self,
        buffer_manager: FrameBufferManager,
        debug_dumper: DebugFrameDumper | None = None,
        sync_matcher: SyncMatcher | None = None,
        processing_queue: ProcessingQueue | None = None,
    ):
        self.buffer_manager = buffer_manager
        self.debug_dumper = debug_dumper
        self.sync_matcher = sync_matcher
        self.processing_queue = processing_queue

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
        if self.sync_matcher is not None and self.processing_queue is not None:
            frame_set = self.sync_matcher.try_match(stored)
            if frame_set is not None:
                self.processing_queue.enqueue(frame_set)
                LOGGER.info(
                    "enqueued synchronized frame_set_id=%s cameras=%s",
                    frame_set.frame_set_id,
                    sorted(frame_set.frames),
                )
        return stored

    def status(self) -> dict:
        return self.buffer_manager.snapshot()

    def camera_statuses(self):
        return self.buffer_manager.camera_statuses()

    def camera_status(self, device_id: str):
        return self.buffer_manager.camera_status(device_id)

    def latest_frame(self, device_id: str):
        return self.buffer_manager.latest_frame(device_id)
