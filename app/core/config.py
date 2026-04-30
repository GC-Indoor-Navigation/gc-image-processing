import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    http_host: str = "127.0.0.1"
    http_port: int = 9000
    grpc_bind: str = "127.0.0.1:50051"
    grpc_enabled: bool = True
    buffer_size: int = 120
    debug_dump_enabled: bool = False
    debug_dump_dir: Path = Path("debug_frames")
    debug_dump_max_per_camera: int = 20
    sync_enabled: bool = False
    sync_window_ms: int = 50
    expected_cameras: tuple[str, ...] = ()
    worker_enabled: bool = True


def load_settings() -> Settings:
    return Settings(
        http_host=os.getenv("PROCESSING_HTTP_HOST", "127.0.0.1"),
        http_port=int(os.getenv("PROCESSING_HTTP_PORT", "9000")),
        grpc_bind=os.getenv("PROCESSING_GRPC_BIND", "127.0.0.1:50051"),
        grpc_enabled=_env_bool("PROCESSING_GRPC_ENABLED", True),
        buffer_size=int(os.getenv("PROCESSING_BUFFER_SIZE", "120")),
        debug_dump_enabled=_env_bool("PROCESSING_DEBUG_DUMP_ENABLED", False),
        debug_dump_dir=Path(os.getenv("PROCESSING_DEBUG_DUMP_DIR", "debug_frames")),
        debug_dump_max_per_camera=int(
            os.getenv("PROCESSING_DEBUG_DUMP_MAX_PER_CAMERA", "20")
        ),
        sync_enabled=_env_bool("PROCESSING_SYNC_ENABLED", False),
        sync_window_ms=int(os.getenv("PROCESSING_SYNC_WINDOW_MS", "50")),
        expected_cameras=_env_list("PROCESSING_EXPECTED_CAMERAS"),
        worker_enabled=_env_bool("PROCESSING_WORKER_ENABLED", True),
    )


def _env_bool(name: str, default: bool) -> bool:
    raw_value = os.getenv(name)
    if raw_value is None:
        return default
    return raw_value.strip().lower() in {"1", "true", "yes", "on"}


def _env_list(name: str) -> tuple[str, ...]:
    raw_value = os.getenv(name, "")
    return tuple(
        item.strip()
        for item in raw_value.split(",")
        if item.strip()
    )
