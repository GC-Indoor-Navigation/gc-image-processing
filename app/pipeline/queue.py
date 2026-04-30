from queue import Empty, Queue
from threading import Lock

from app.models.frame import SynchronizedFrameSet


class ProcessingQueue:
    def __init__(self):
        self._queue: Queue[SynchronizedFrameSet] = Queue()
        self._lock = Lock()
        self.enqueued_count = 0
        self.dequeued_count = 0

    def enqueue(self, frame_set: SynchronizedFrameSet):
        self._queue.put(frame_set)
        with self._lock:
            self.enqueued_count += 1

    def get(self, timeout: float = 0.5) -> SynchronizedFrameSet | None:
        try:
            frame_set = self._queue.get(timeout=timeout)
        except Empty:
            return None
        with self._lock:
            self.dequeued_count += 1
        return frame_set

    def task_done(self):
        self._queue.task_done()

    def status(self):
        with self._lock:
            return {
                "queue_size": self._queue.qsize(),
                "enqueued_count": self.enqueued_count,
                "dequeued_count": self.dequeued_count,
            }

