from app.infrastructure.debug_dump import DebugFrameDumper
from app.models.frame import StoredFrame


def make_frame(sequence: int):
    return StoredFrame(
        device_id="camera1",
        timestamp_ms=1000 + sequence,
        sequence=sequence,
        content_type="image/jpeg",
        image_bytes=b"frame",
        image_size=5,
        source_file_path=None,
    )


def test_debug_dump_writes_frame_when_enabled(tmp_path):
    dumper = DebugFrameDumper(
        enabled=True,
        dump_dir=tmp_path,
        max_per_camera=20,
    )

    path = dumper.dump(make_frame(1))

    assert path is not None
    assert path.exists()
    assert path.read_bytes() == b"frame"


def test_debug_dump_is_noop_when_disabled(tmp_path):
    dumper = DebugFrameDumper(
        enabled=False,
        dump_dir=tmp_path,
        max_per_camera=20,
    )

    path = dumper.dump(make_frame(1))

    assert path is None
    assert list(tmp_path.iterdir()) == []


def test_debug_dump_prunes_old_frames(tmp_path):
    dumper = DebugFrameDumper(
        enabled=True,
        dump_dir=tmp_path,
        max_per_camera=1,
    )

    dumper.dump(make_frame(1))
    dumper.dump(make_frame(2))

    files = list((tmp_path / "camera1").iterdir())
    assert len(files) == 1
    assert files[0].name.startswith("00000002_")
