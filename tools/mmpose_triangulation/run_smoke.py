from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.pipeline.input_adapter import CameraFrameInput, MotionCaptureInput
from app.pipeline.mmpose_triangulation import (
    MMPoseTriangulationConfig,
    MMPoseTriangulationProcessor,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run one MMPose triangulation smoke frame set."
    )
    parser.add_argument("--calib-json", required=True)
    parser.add_argument(
        "--camera-frame",
        action="append",
        required=True,
        help=(
            "Frame input in device_id=CalibrationCameraName=image_path format. "
            "Example: camera1=Camera1=C:/frames/camera1.jpg"
        ),
    )
    parser.add_argument("--out-json", required=True)
    parser.add_argument("--frame-set-id", type=int, default=1)
    parser.add_argument("--anchor-timestamp-ms", type=int, default=0)
    parser.add_argument("--max-delta-ms", type=int, default=0)
    parser.add_argument("--pose2d", default="human")
    parser.add_argument("--device", default="cuda:0")
    parser.add_argument("--kpt-thr", type=float, default=0.30)
    parser.add_argument("--max-reproj-error", type=float, default=40.0)
    parser.add_argument("--images-undistorted", action="store_true")
    parser.add_argument("--extrinsic-source", default="auto")
    parser.add_argument("--extrinsic-convention", default="world_to_camera")
    parser.add_argument("--temp-dir", default=None)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    frame_specs = [parse_camera_frame_arg(item) for item in args.camera_frame]
    camera_mapping = {spec.device_id: spec.camera_name for spec in frame_specs}
    processing_input = build_motion_capture_input(
        frame_specs=frame_specs,
        frame_set_id=args.frame_set_id,
        anchor_timestamp_ms=args.anchor_timestamp_ms,
        max_delta_ms=args.max_delta_ms,
    )

    processor = MMPoseTriangulationProcessor(
        MMPoseTriangulationConfig(
            calib_json=Path(args.calib_json),
            camera_mapping=camera_mapping,
            pose2d=args.pose2d,
            device=args.device,
            kpt_thr=args.kpt_thr,
            max_reproj_error=args.max_reproj_error,
            images_undistorted=args.images_undistorted,
            extrinsic_source=args.extrinsic_source,
            extrinsic_convention=args.extrinsic_convention,
            temp_dir=Path(args.temp_dir) if args.temp_dir else None,
        )
    )

    processing_result = processor.process(processing_input)
    skeleton_result = processor.last_skeleton_result
    out = {
        "processing_result": asdict(processing_result),
        "skeleton_result": asdict(skeleton_result) if skeleton_result else None,
    }

    out_json = Path(args.out_json)
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(
        json.dumps(out, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print("[DONE] MMPose triangulation smoke")
    print(f"  status: {processing_result.status}")
    print(f"  elapsed_ms: {processing_result.elapsed_ms:.3f}")
    if skeleton_result is not None:
        print(f"  valid_joints: {skeleton_result.num_valid_joints}")
        print(f"  avg_reproj_error_px: {skeleton_result.avg_reproj_error_px}")
    print(f"  output_json: {out_json}")
    return 0


class CameraFrameSpec:
    def __init__(self, device_id: str, camera_name: str, image_path: Path):
        self.device_id = device_id
        self.camera_name = camera_name
        self.image_path = image_path


def parse_camera_frame_arg(value: str) -> CameraFrameSpec:
    parts = value.split("=", 2)
    if len(parts) != 3:
        raise ValueError(
            "camera-frame must use device_id=CalibrationCameraName=image_path format"
        )
    device_id, camera_name, image_path = (part.strip() for part in parts)
    if not device_id or not camera_name or not image_path:
        raise ValueError(
            "camera-frame must include device ID, calibration camera name, and image path"
        )
    path = Path(image_path)
    if not path.exists():
        raise FileNotFoundError(f"camera frame image not found: {path}")
    if not path.is_file():
        raise ValueError(f"camera frame path is not a file: {path}")
    return CameraFrameSpec(device_id=device_id, camera_name=camera_name, image_path=path)


def build_motion_capture_input(
    frame_specs: list[CameraFrameSpec],
    frame_set_id: int,
    anchor_timestamp_ms: int,
    max_delta_ms: int,
) -> MotionCaptureInput:
    frames = {}
    for sequence, spec in enumerate(frame_specs, start=1):
        image_bytes = spec.image_path.read_bytes()
        frames[spec.device_id] = CameraFrameInput(
            device_id=spec.device_id,
            timestamp_ms=anchor_timestamp_ms,
            sequence=sequence,
            content_type=content_type_from_suffix(spec.image_path),
            image_bytes=image_bytes,
            image_size=len(image_bytes),
            source_file_path=str(spec.image_path),
            source_frame_id=sequence,
        )

    return MotionCaptureInput(
        frame_set_id=frame_set_id,
        anchor_timestamp_ms=anchor_timestamp_ms,
        max_delta_ms=max_delta_ms,
        frames=frames,
    )


def content_type_from_suffix(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in {".jpg", ".jpeg"}:
        return "image/jpeg"
    if suffix == ".png":
        return "image/png"
    if suffix == ".webp":
        return "image/webp"
    return "application/octet-stream"


if __name__ == "__main__":
    raise SystemExit(main())
