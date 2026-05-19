import logging
from threading import Lock

from app.buffers.frame_buffer import FrameBufferManager
from app.infrastructure.debug_dump import DebugFrameDumper
from app.infrastructure.relay_contract import RelayFrame, RelayFrameSet
from app.models.frame import IncomingFrame, StoredFrame
from app.models.frame import SynchronizedFrameSet
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
        self.accepted_relay_frame_set_count = 0
        self.duplicate_relay_frame_set_count = 0
        self.last_relay_frame_set_id: int | None = None
        self._seen_relay_frame_set_ids: set[int] = set()
        self._relay_frame_set_lock = Lock()

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

    def handle_relay_frame_set(
        self,
        frame_set: RelayFrameSet,
    ) -> SynchronizedFrameSet | None:
        with self._relay_frame_set_lock:
            if frame_set.frame_set_id in self._seen_relay_frame_set_ids:
                self.duplicate_relay_frame_set_count += 1
                LOGGER.info(
                    "ignored duplicate relay frame_set_id=%s",
                    frame_set.frame_set_id,
                )
                return None

            self._seen_relay_frame_set_ids.add(frame_set.frame_set_id)
            synchronized_frame_set = self._relay_frame_set_to_synchronized(frame_set)
            if self.processing_queue is not None:
                self.processing_queue.enqueue(synchronized_frame_set)
            self.accepted_relay_frame_set_count += 1
            self.last_relay_frame_set_id = frame_set.frame_set_id

        LOGGER.info(
            "enqueued relay frame_set_id=%s cameras=%s",
            frame_set.frame_set_id,
            sorted(synchronized_frame_set.frames),
        )
        return synchronized_frame_set

    def relay_frame_set_status(self):
        with self._relay_frame_set_lock:
            return {
                "accepted_count": self.accepted_relay_frame_set_count,
                "duplicate_count": self.duplicate_relay_frame_set_count,
                "last_frame_set_id": self.last_relay_frame_set_id,
            }

    def status(self) -> dict:
        return self.buffer_manager.snapshot()

    def camera_statuses(self):
        return self.buffer_manager.camera_statuses()

    def camera_status(self, device_id: str):
        return self.buffer_manager.camera_status(device_id)

    def latest_frame(self, device_id: str):
        return self.buffer_manager.latest_frame(device_id)

    def _relay_frame_set_to_synchronized(
        self,
        frame_set: RelayFrameSet,
    ) -> SynchronizedFrameSet:
        return SynchronizedFrameSet(
            frame_set_id=frame_set.frame_set_id,
            anchor_timestamp_ms=frame_set.anchor_timestamp_ms,
            max_delta_ms=frame_set.max_delta_ms,
            frames={
                frame.device_id: StoredFrame(
                    device_id=frame.device_id,
                    timestamp_ms=frame.timestamp_ms,
                    sequence=frame.sequence,
                    content_type=frame.content_type,
                    image_bytes=frame.image_bytes,
                    image_size=len(frame.image_bytes),
                    source_file_path=frame.file_path,
                    source_frame_id=frame.frame_id,
                )
                for frame in frame_set.frames
            },
        )
