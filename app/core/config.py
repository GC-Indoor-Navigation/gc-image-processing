import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ENV_PATH = PROJECT_ROOT / ".env"


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
    processor: str = "placeholder"
    mmpose_calib_json: Path | None = None
    mmpose_camera_mapping: tuple[str, ...] = ()
    mmpose_pose2d: str = "human"
    mmpose_device: str = "cuda:0"
    mmpose_kpt_thr: float = 0.30
    mmpose_max_reproj_error: float = 40.0
    mmpose_images_undistorted: bool = False
    mmpose_extrinsic_source: str = "auto"
    mmpose_extrinsic_convention: str = "world_to_camera"
    mmpose_temp_dir: Path | None = None
    mmpose_preload: bool = False


def load_settings() -> Settings:
    load_dotenv(DEFAULT_ENV_PATH)
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
        processor=os.getenv("PROCESSING_PROCESSOR", "placeholder"),
        mmpose_calib_json=_env_optional_path("PROCESSING_MMPOSE_CALIB_JSON"),
        mmpose_camera_mapping=_env_list("PROCESSING_MMPOSE_CAMERA_MAPPING"),
        mmpose_pose2d=os.getenv("PROCESSING_MMPOSE_POSE2D", "human"),
        mmpose_device=os.getenv("PROCESSING_MMPOSE_DEVICE", "cuda:0"),
        mmpose_kpt_thr=float(os.getenv("PROCESSING_MMPOSE_KPT_THR", "0.30")),
        mmpose_max_reproj_error=float(
            os.getenv("PROCESSING_MMPOSE_MAX_REPROJ_ERROR", "40.0")
        ),
        mmpose_images_undistorted=_env_bool(
            "PROCESSING_MMPOSE_IMAGES_UNDISTORTED",
            False,
        ),
        mmpose_extrinsic_source=os.getenv(
            "PROCESSING_MMPOSE_EXTRINSIC_SOURCE",
            "auto",
        ),
        mmpose_extrinsic_convention=os.getenv(
            "PROCESSING_MMPOSE_EXTRINSIC_CONVENTION",
            "world_to_camera",
        ),
        mmpose_temp_dir=_env_optional_path("PROCESSING_MMPOSE_TEMP_DIR"),
        mmpose_preload=_env_bool("PROCESSING_MMPOSE_PRELOAD", False),
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


def _env_optional_path(name: str) -> Path | None:
    raw_value = os.getenv(name, "").strip()
    if not raw_value:
        return None
    return Path(raw_value)
