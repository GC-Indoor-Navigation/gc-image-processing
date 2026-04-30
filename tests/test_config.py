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
    )
