from pathlib import Path

from app.models.frame import StoredFrame


class DebugFrameDumper:
    def __init__(
        self,
        enabled: bool,
        dump_dir: Path,
        max_per_camera: int,
    ):
        self.enabled = enabled
        self.dump_dir = dump_dir
        self.max_per_camera = max_per_camera

    def dump(self, frame: StoredFrame):
        if not self.enabled:
            return None

        camera_dir = self.dump_dir / _safe_path_segment(frame.device_id)
        camera_dir.mkdir(parents=True, exist_ok=True)
        path = camera_dir / _build_filename(frame)
        path.write_bytes(frame.image_bytes)
        self._prune(camera_dir)
        return path

    def _prune(self, camera_dir: Path):
        if self.max_per_camera <= 0:
            return
        files = sorted(
            (path for path in camera_dir.iterdir() if path.is_file()),
            key=lambda path: path.name,
            reverse=True,
        )
        for path in files[self.max_per_camera :]:
            path.unlink(missing_ok=True)


def _build_filename(frame: StoredFrame) -> str:
    extension = _extension_for_content_type(frame.content_type)
    return f"{frame.sequence:08d}_{frame.timestamp_ms}{extension}"


def _extension_for_content_type(content_type: str) -> str:
    normalized = content_type.lower().split(";")[0].strip()
    if normalized == "image/png":
        return ".png"
    if normalized in {"image/jpg", "image/jpeg"}:
        return ".jpg"
    return ".bin"


def _safe_path_segment(value: str) -> str:
    return "".join(char if char.isalnum() or char in {"-", "_"} else "_" for char in value)
