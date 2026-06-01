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
            "triangulation_summary": _to_summary(skeleton_result),
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

    def read_history(
        self,
        limit: int = 20,
        run_key: str | None = None,
    ) -> list[dict[str, Any]]:
        limit = max(1, min(limit, 500))
        entries = self._read_all_entries(run_key=run_key)
        entries.sort(
            key=lambda entry: (
                entry.get("written_at") or 0,
                entry.get("relay_run_id") or 0,
                entry.get("frame_set_id") or 0,
            ),
            reverse=True,
        )
        return [_to_history_item(entry) for entry in entries[:limit]]

    def read_detail(
        self,
        frame_set_id: int,
        run_key: str | None = None,
    ) -> dict[str, Any] | None:
        matches = []
        for path in self._result_paths(run_key=run_key):
            for entry in _read_jsonl(path):
                if entry.get("frame_set_id") != frame_set_id:
                    continue
                matches.append((path, entry))
        if not matches:
            return None
        path, entry = max(
            matches,
            key=lambda item: item[1].get("written_at") or 0,
        )
        return {
            "run_key": path.stem,
            "path": str(path),
            "written_at": entry.get("written_at"),
            "relay_run_id": entry.get("relay_run_id"),
            "frame_set_id": entry.get("frame_set_id"),
            "processing_result": entry.get("processing_result"),
            "triangulation_summary": entry.get("triangulation_summary"),
        }

    def summarize(self) -> dict[str, Any]:
        runs = []
        for path in self._result_paths():
            entries = _read_jsonl(path)
            if not entries:
                continue
            runs.append(_summarize_result_file(path, entries))

        return {
            "enabled": True,
            "output_dir": str(self.output_dir),
            "run_count": len(runs),
            "runs": runs,
        }

    def _path_for_run(self, run_id: int) -> Path:
        path = self._paths_by_run_id.get(run_id)
        if path is None:
            timestamp = datetime.now().strftime("%Y%m%dT%H%M%S")
            path = self.output_dir / f"relay_run_{run_id:04d}_{timestamp}.jsonl"
            self._paths_by_run_id[run_id] = path
        return path

    def _read_all_entries(self, run_key: str | None = None) -> list[dict[str, Any]]:
        entries = []
        for path in self._result_paths(run_key=run_key):
            entries.extend(_read_jsonl(path))
        return entries

    def _result_paths(self, run_key: str | None = None) -> list[Path]:
        paths = set(self._paths_by_run_id.values())
        if self.output_dir.exists():
            paths.update(self.output_dir.glob("relay_run_*.jsonl"))
        if run_key is not None:
            paths = {path for path in paths if path.stem == run_key}
        return sorted(paths, key=lambda path: path.stat().st_mtime if path.exists() else 0)


def _to_jsonable(value: Any) -> Any:
    if value is None:
        return None
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, dict):
        return value
    return value


def _to_summary(value: Any) -> dict[str, Any] | None:
    result = _to_jsonable(value)
    if result is None:
        return None
    source_frames = result.get("source_frames") or {}
    return {
        "frame_set_id": result.get("frame_set_id"),
        "anchor_timestamp_ms": result.get("anchor_timestamp_ms"),
        "max_delta_ms": result.get("max_delta_ms"),
        "num_valid_joints": result.get("num_valid_joints"),
        "avg_reproj_error_px": result.get("avg_reproj_error_px"),
        "joints_world": {
            joint_name: joint.get("xyz")
            for joint_name, joint in (result.get("joints_world") or {}).items()
            if isinstance(joint, dict)
        },
        "joint_scores": {
            joint_name: joint.get("score")
            for joint_name, joint in (result.get("joints_world") or {}).items()
            if isinstance(joint, dict)
        },
        "joint_reproj_error_px": {
            joint_name: joint.get("reproj_error_px")
            for joint_name, joint in (result.get("joints_world") or {}).items()
            if isinstance(joint, dict)
        },
        "source_frames": {
            camera_name: {
                "device_id": frame.get("device_id"),
                "timestamp_ms": frame.get("timestamp_ms"),
                "sequence": frame.get("sequence"),
                "source_file_path": frame.get("source_file_path"),
                "source_frame_id": frame.get("source_frame_id"),
            }
            for camera_name, frame in source_frames.items()
            if isinstance(frame, dict)
        },
    }


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    entries = []
    with path.open("r", encoding="utf-8") as file:
        for line in file:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(entry, dict):
                entries.append(entry)
    return entries


def _to_history_item(entry: dict[str, Any]) -> dict[str, Any]:
    processing_result = entry.get("processing_result") or {}
    summary = entry.get("triangulation_summary") or {}
    return {
        "written_at": entry.get("written_at"),
        "relay_run_id": entry.get("relay_run_id"),
        "frame_set_id": entry.get("frame_set_id"),
        "status": processing_result.get("status"),
        "elapsed_ms": processing_result.get("elapsed_ms"),
        "num_valid_joints": summary.get("num_valid_joints"),
        "avg_reproj_error_px": summary.get("avg_reproj_error_px"),
        "max_delta_ms": summary.get("max_delta_ms"),
        "source_frames": summary.get("source_frames") or {},
    }


def _summarize_result_file(
    path: Path,
    entries: list[dict[str, Any]],
) -> dict[str, Any]:
    valid_joint_counts = []
    reproj_errors = []
    elapsed_times = []
    status_counts: dict[str, int] = {}
    frame_set_ids = []
    relay_run_ids = set()
    worst_reproj_frame_set_id = None
    worst_reproj_error = None
    slowest_frame_set_id = None
    max_elapsed_ms = None

    for entry in entries:
        processing_result = entry.get("processing_result") or {}
        summary = entry.get("triangulation_summary") or {}
        relay_run_id = entry.get("relay_run_id")
        if isinstance(relay_run_id, int):
            relay_run_ids.add(relay_run_id)
        status = processing_result.get("status") or "unknown"
        status_counts[status] = status_counts.get(status, 0) + 1
        frame_set_id = entry.get("frame_set_id")
        if isinstance(frame_set_id, int):
            frame_set_ids.append(frame_set_id)
        _append_number(valid_joint_counts, summary.get("num_valid_joints"))
        reproj_error = summary.get("avg_reproj_error_px")
        elapsed_ms = processing_result.get("elapsed_ms")
        _append_number(reproj_errors, reproj_error)
        _append_number(elapsed_times, elapsed_ms)
        if (
            isinstance(frame_set_id, int)
            and isinstance(reproj_error, int | float)
            and (worst_reproj_error is None or reproj_error > worst_reproj_error)
        ):
            worst_reproj_error = float(reproj_error)
            worst_reproj_frame_set_id = frame_set_id
        if (
            isinstance(frame_set_id, int)
            and isinstance(elapsed_ms, int | float)
            and (max_elapsed_ms is None or elapsed_ms > max_elapsed_ms)
        ):
            max_elapsed_ms = float(elapsed_ms)
            slowest_frame_set_id = frame_set_id

    return {
        "relay_run_id": min(relay_run_ids) if relay_run_ids else 0,
        "run_key": path.stem,
        "path": str(path),
        "result_count": len(entries),
        "first_frame_set_id": min(frame_set_ids) if frame_set_ids else None,
        "last_frame_set_id": max(frame_set_ids) if frame_set_ids else None,
        "status_counts": status_counts,
        "avg_valid_joints": _avg(valid_joint_counts),
        "avg_reproj_error_px": _avg(reproj_errors),
        "min_reproj_error_px": min(reproj_errors) if reproj_errors else None,
        "max_reproj_error_px": max(reproj_errors) if reproj_errors else None,
        "avg_elapsed_ms": _avg(elapsed_times),
        "worst_reproj_frame_set_id": worst_reproj_frame_set_id,
        "slowest_frame_set_id": slowest_frame_set_id,
        "max_elapsed_ms": max_elapsed_ms,
    }


def _append_number(values: list[float], value: Any):
    if isinstance(value, int | float):
        values.append(float(value))


def _avg(values: list[float]) -> float | None:
    if not values:
        return None
    return sum(values) / len(values)
