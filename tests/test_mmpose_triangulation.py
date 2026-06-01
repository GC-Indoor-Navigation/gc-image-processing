import pytest

from app.core.config import Settings
from app.main import build_motion_capture_processor
from app.pipeline.input_adapter import CameraFrameInput, MotionCaptureInput
from app.pipeline.mmpose_triangulation import (
    MMPoseTriangulationConfig,
    MMPoseTriangulationProcessor,
    load_calibrations,
    parse_camera_mapping,
    write_frame_set_images,
)


def test_parse_camera_mapping_parses_device_to_calibration_names():
    mapping = parse_camera_mapping(("camera1=Camera1", "camera2=Camera2"))

    assert mapping == {"camera1": "Camera1", "camera2": "Camera2"}


def test_parse_camera_mapping_rejects_invalid_item():
    with pytest.raises(ValueError, match="device_id=CalibrationCameraName"):
        parse_camera_mapping(("camera1",))


def test_write_frame_set_images_uses_camera_mapping(tmp_path):
    processing_input = MotionCaptureInput(
        frame_set_id=7,
        anchor_timestamp_ms=1000,
        max_delta_ms=10,
        frames={
            "camera1": CameraFrameInput(
                device_id="camera1",
                timestamp_ms=1000,
                sequence=1,
                content_type="image/jpeg",
                image_bytes=b"jpeg-data",
                image_size=9,
                source_file_path=None,
                source_frame_id=101,
            )
        },
    )

    result = write_frame_set_images(
        processing_input,
        {"camera1": "Camera1"},
        tmp_path,
    )

    assert set(result) == {"Camera1"}
    assert result["Camera1"].suffix == ".jpg"
    assert result["Camera1"].read_bytes() == b"jpeg-data"


def test_write_frame_set_images_requires_all_mapped_cameras(tmp_path):
    processing_input = MotionCaptureInput(
        frame_set_id=7,
        anchor_timestamp_ms=1000,
        max_delta_ms=10,
        frames={},
    )

    with pytest.raises(ValueError, match="missing required cameras"):
        write_frame_set_images(
            processing_input,
            {"camera1": "Camera1"},
            tmp_path,
        )


def test_build_motion_capture_processor_builds_mmpose_processor(tmp_path):
    processor = build_motion_capture_processor(
        Settings(
            processor="mmpose_triangulation",
            mmpose_calib_json=tmp_path / "calibration.json",
            mmpose_camera_mapping=("camera1=Camera1",),
        )
    )

    assert isinstance(processor, MMPoseTriangulationProcessor)
    assert processor.config.camera_mapping == {"camera1": "Camera1"}


def test_mmpose_processor_prepare_loads_calibration_and_inferencer(tmp_path):
    loaded = []

    def calibration_loader(
        calib_json,
        camera_names,
        extrinsic_source,
        extrinsic_convention,
    ):
        loaded.append((calib_json, camera_names, extrinsic_source, extrinsic_convention))
        return {"Camera1": object()}

    processor = MMPoseTriangulationProcessor(
        config=MMPoseTriangulationConfig(
            calib_json=tmp_path / "calibration.json",
            camera_mapping={"camera1": "Camera1"},
        ),
        inferencer=object(),
        calibration_loader=calibration_loader,
    )

    processor.prepare()

    assert loaded == [
        (
            str(tmp_path / "calibration.json"),
            ["Camera1"],
            "auto",
            "world_to_camera",
        )
    ]


def test_build_motion_capture_processor_requires_mmpose_calibration():
    with pytest.raises(ValueError, match="PROCESSING_MMPOSE_CALIB_JSON"):
        build_motion_capture_processor(
            Settings(
                processor="mmpose_triangulation",
                mmpose_camera_mapping=("camera1=Camera1",),
            )
        )


def test_load_calibrations_reads_final_cascalib_bundle_adjusted_json(tmp_path):
    np = pytest.importorskip("numpy")
    calib_path = tmp_path / "final_cascalib_with_intrinsics_extrinsics_sync.json"
    calib_path.write_text(
        """
        {
          "result_type": "cascalib_final_with_intrinsics_extrinsics_sync",
          "cameras": [
            {
              "camera": "Camera1",
              "image_size": {"width": 1920, "height": 1080},
              "intrinsics": {
                "camera_matrix": [[1000, 0, 960], [0, 1000, 540], [0, 0, 1]],
                "dist_coeffs": [0.1, 0.01, 0, 0, 0]
              },
              "bundle_adjusted": {
                "intrinsics": [[1100, 0, 950], [0, 1110, 545], [0, 0, 1]],
                "rotation_world_to_camera": [[1, 0, 0], [0, 1, 0], [0, 0, 1]],
                "camera_center_world": [1, 2, 3]
              }
            }
          ]
        }
        """,
        encoding="utf-8",
    )

    calibs = load_calibrations(
        str(calib_path),
        ["Camera1"],
        "bundle_adjusted",
        "world_to_camera",
    )

    calib = calibs["Camera1"]
    assert calib.width == 1920
    assert calib.height == 1080
    assert calib.K.tolist() == [[1100.0, 0.0, 950.0], [0.0, 1110.0, 545.0], [0.0, 0.0, 1.0]]
    assert calib.dist.tolist() == [0.1, 0.01, 0.0, 0.0, 0.0]
    assert np.allclose(calib.t, [-1.0, -2.0, -3.0])
    assert np.allclose(calib.center_world, [1.0, 2.0, 3.0])


def test_load_calibrations_reads_vggt_style_root_camera_json(tmp_path):
    np = pytest.importorskip("numpy")
    calib_path = tmp_path / "voxelpose_calibration.json"
    calib_path.write_text(
        """
        {
          "cameras": [
            {
              "camera": "Camera1",
              "K": [[1000, 0, 960], [0, 1000, 540], [0, 0, 1]],
              "R": [[1, 0, 0], [0, 1, 0], [0, 0, 1]],
              "camera_center_world": [4, 5, 6],
              "distCoef": [0, 0, 0, 0, 0],
              "image_size": {"width": 1920, "height": 1080}
            }
          ]
        }
        """,
        encoding="utf-8",
    )

    calibs = load_calibrations(
        str(calib_path),
        ["Camera1"],
        "auto",
        "world_to_camera",
    )

    calib = calibs["Camera1"]
    assert calib.K.shape == (3, 3)
    assert calib.R.shape == (3, 3)
    assert np.allclose(calib.t, [-4.0, -5.0, -6.0])
