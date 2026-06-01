from pathlib import Path

from app.core.config import Settings, load_settings


def test_load_settings_reads_project_env_file(tmp_path, monkeypatch):
    for name in [
        "PROCESSING_HTTP_HOST",
        "PROCESSING_HTTP_PORT",
        "PROCESSING_GRPC_BIND",
        "PROCESSING_GRPC_ENABLED",
        "PROCESSING_BUFFER_SIZE",
        "PROCESSING_DEBUG_DUMP_ENABLED",
        "PROCESSING_DEBUG_DUMP_DIR",
        "PROCESSING_DEBUG_DUMP_MAX_PER_CAMERA",
        "PROCESSING_SYNC_ENABLED",
        "PROCESSING_SYNC_WINDOW_MS",
        "PROCESSING_EXPECTED_CAMERAS",
        "PROCESSING_WORKER_ENABLED",
        "PROCESSING_PROCESSOR",
        "PROCESSING_MMPOSE_CALIB_JSON",
        "PROCESSING_MMPOSE_CAMERA_MAPPING",
        "PROCESSING_MMPOSE_POSE2D",
        "PROCESSING_MMPOSE_DEVICE",
        "PROCESSING_MMPOSE_KPT_THR",
        "PROCESSING_MMPOSE_MAX_REPROJ_ERROR",
        "PROCESSING_MMPOSE_IMAGES_UNDISTORTED",
        "PROCESSING_MMPOSE_EXTRINSIC_SOURCE",
        "PROCESSING_MMPOSE_EXTRINSIC_CONVENTION",
        "PROCESSING_MMPOSE_TEMP_DIR",
        "PROCESSING_MMPOSE_PRELOAD",
    ]:
        monkeypatch.delenv(name, raising=False)

    env_path = tmp_path / ".env"
    env_path.write_text(
        "\n".join(
            [
                "PROCESSING_HTTP_HOST=0.0.0.0",
                "PROCESSING_HTTP_PORT=9100",
                "PROCESSING_GRPC_BIND=0.0.0.0:51051",
                "PROCESSING_GRPC_ENABLED=false",
                "PROCESSING_BUFFER_SIZE=10",
                "PROCESSING_SYNC_ENABLED=true",
                "PROCESSING_SYNC_WINDOW_MS=75",
                "PROCESSING_EXPECTED_CAMERAS=camera1,camera2",
                "PROCESSING_WORKER_ENABLED=false",
                "PROCESSING_PROCESSOR=mmpose_triangulation",
                "PROCESSING_MMPOSE_CALIB_JSON=calibration.json",
                "PROCESSING_MMPOSE_CAMERA_MAPPING=camera1=Camera1,camera2=Camera2",
                "PROCESSING_MMPOSE_POSE2D=rtmpose-m",
                "PROCESSING_MMPOSE_DEVICE=cpu",
                "PROCESSING_MMPOSE_KPT_THR=0.5",
                "PROCESSING_MMPOSE_MAX_REPROJ_ERROR=12.5",
                "PROCESSING_MMPOSE_IMAGES_UNDISTORTED=true",
                "PROCESSING_MMPOSE_EXTRINSIC_SOURCE=bundle_adjusted",
                "PROCESSING_MMPOSE_EXTRINSIC_CONVENTION=camera_to_world",
                "PROCESSING_MMPOSE_TEMP_DIR=tmp/mmpose",
                "PROCESSING_MMPOSE_PRELOAD=true",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr("app.core.config.DEFAULT_ENV_PATH", env_path)

    settings = load_settings()

    assert settings == Settings(
        http_host="0.0.0.0",
        http_port=9100,
        grpc_bind="0.0.0.0:51051",
        grpc_enabled=False,
        buffer_size=10,
        sync_enabled=True,
        sync_window_ms=75,
        expected_cameras=("camera1", "camera2"),
        worker_enabled=False,
        processor="mmpose_triangulation",
        mmpose_calib_json=Path("calibration.json"),
        mmpose_camera_mapping=("camera1=Camera1", "camera2=Camera2"),
        mmpose_pose2d="rtmpose-m",
        mmpose_device="cpu",
        mmpose_kpt_thr=0.5,
        mmpose_max_reproj_error=12.5,
        mmpose_images_undistorted=True,
        mmpose_extrinsic_source="bundle_adjusted",
        mmpose_extrinsic_convention="camera_to_world",
        mmpose_temp_dir=Path("tmp/mmpose"),
        mmpose_preload=True,
    )
