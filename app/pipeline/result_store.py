import json
from dataclasses import asdict, is_dataclass
from datetime import datetime
from pathlib import Path
from threading import Lock
from time import time
from typing import Any

from app.models.frame import SynchronizedFrameSet
from app.pipeline.processor import ProcessingResult


class JsonlTriangulationResultStore:
    def __init__(self, output_dir: Path):
        self.output_dir = output_dir
        self._lock = Lock()
        self._paths_by_run_id: dict[int, Path] = {}
        self._written_count_by_run_id: dict[int, int] = {}
        self.last_written_path: Path | None = None

    def save(
        self,
        frame_set: SynchronizedFrameSet,
        processing_result: ProcessingResult,
        skeleton_result: Any | None,
    ) -> Path:
        run_id = frame_set.relay_run_id or 0
        entry = {
            "written_at": time(),
            "relay_run_id": run_id,
            "frame_set_id": frame_set.frame_set_id,
            "processing_result": asdict(processing_result),
            "triangulation_result": _to_jsonable(skeleton_result),
        }
        line = json.dumps(entry, ensure_ascii=False, separators=(",", ":"))

        with self._lock:
            path = self._path_for_run(run_id)
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as file:
                file.write(line)
                file.write("\n")
            self._written_count_by_run_id[run_id] = (
                self._written_count_by_run_id.get(run_id, 0) + 1
            )
            self.last_written_path = path
            return path

    def status(self) -> dict[str, Any]:
        with self._lock:
            return {
                "enabled": True,
                "output_dir": str(self.output_dir),
                "last_written_path": (
                    str(self.last_written_path)
                    if self.last_written_path is not None
                    else None
                ),
                "runs": {
                    str(run_id): {
                        "path": str(path),
                        "written_count": self._written_count_by_run_id.get(
                            run_id,
                            0,
                        ),
                    }
                    for run_id, path in sorted(self._paths_by_run_id.items())
                },
            }

    def _path_for_run(self, run_id: int) -> Path:
        path = self._paths_by_run_id.get(run_id)
        if path is None:
            timestamp = datetime.now().strftime("%Y%m%dT%H%M%S")
            path = self.output_dir / f"relay_run_{run_id:04d}_{timestamp}.jsonl"
            self._paths_by_run_id[run_id] = path
        return path


def _to_jsonable(value: Any) -> Any:
    if value is None:
        return None
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, dict):
        return value
    return value
