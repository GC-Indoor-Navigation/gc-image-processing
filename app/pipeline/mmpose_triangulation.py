import json
import math
import re
import tempfile
from dataclasses import dataclass
from itertools import combinations
from pathlib import Path
from time import time
from typing import Any

from app.pipeline.input_adapter import MotionCaptureInput
from app.pipeline.processor import ProcessingResult


COCO17_NAMES = [
    "nose",
    "left_eye",
    "right_eye",
    "left_ear",
    "right_ear",
    "left_shoulder",
    "right_shoulder",
    "left_elbow",
    "right_elbow",
    "left_wrist",
    "right_wrist",
    "left_hip",
    "right_hip",
    "left_knee",
    "right_knee",
    "left_ankle",
    "right_ankle",
]


@dataclass(frozen=True)
class MMPoseTriangulationConfig:
    calib_json: Path
    camera_mapping: dict[str, str]
    pose2d: str = "human"
    device: str = "cuda:0"
    kpt_thr: float = 0.30
    max_reproj_error: float = 40.0
    images_undistorted: bool = False
    extrinsic_source: str = "auto"
    extrinsic_convention: str = "world_to_camera"
    temp_dir: Path | None = None


@dataclass(frozen=True)
class CameraCalibration:
    name: str
    K: Any
    dist: Any
    R: Any
    t: Any
    width: int | None = None
    height: int | None = None

    @property
    def P(self):
        np = _np()
        Rt = np.hstack([self.R, self.t.reshape(3, 1)])
        return self.K @ Rt

    @property
    def center_world(self):
        return -self.R.T @ self.t.reshape(3)

    def world_to_camera(self, Xw):
        return (self.R @ Xw.reshape(3)) + self.t.reshape(3)


@dataclass(frozen=True)
class TriangulatedFrameResult:
    frame_set_id: int
    anchor_timestamp_ms: int
    max_delta_ms: int
    num_valid_joints: int
    avg_reproj_error_px: float | None
    joints_world: dict[str, dict[str, Any]]
    joints_camera: dict[str, dict[str, Any]]
    camera_centers_world: dict[str, list[float]]
    source_frames: dict[str, dict[str, Any]]


class MMPoseTriangulationProcessor:
    def __init__(
        self,
        config: MMPoseTriangulationConfig,
        inferencer: Any | None = None,
        calibration_loader=None,
    ):
        self.config = config
        self._inferencer = inferencer
        self._calibration_loader = calibration_loader or load_calibrations
        self._calibrations: dict[str, CameraCalibration] | None = None
        self.last_skeleton_result: TriangulatedFrameResult | None = None

    def process(self, processing_input: MotionCaptureInput) -> ProcessingResult:
        started_at = time()
        self._ensure_ready()
        assert self._calibrations is not None
        assert self._inferencer is not None

        with tempfile.TemporaryDirectory(
            dir=str(self.config.temp_dir) if self.config.temp_dir else None
        ) as tmp:
            frame_paths = write_frame_set_images(
                processing_input=processing_input,
                camera_mapping=self.config.camera_mapping,
                output_dir=Path(tmp),
            )
            self.last_skeleton_result = triangulate_frame_set(
                processing_input=processing_input,
                frame_paths=frame_paths,
                camera_mapping=self.config.camera_mapping,
                camera_names=list(self.config.camera_mapping.values()),
                calibs=self._calibrations,
                inferencer=self._inferencer,
                kpt_thr=self.config.kpt_thr,
                max_reproj_error=self.config.max_reproj_error,
                images_undistorted=self.config.images_undistorted,
            )

        finished_at = time()
        status = (
            "mmpose_triangulated"
            if self.last_skeleton_result.num_valid_joints > 0
            else "mmpose_no_valid_joints"
        )
        return ProcessingResult(
            frame_set_id=processing_input.frame_set_id,
            status=status,
            camera_count=len(processing_input.frames),
            started_at=started_at,
            finished_at=finished_at,
            elapsed_ms=(finished_at - started_at) * 1000,
        )

    def prepare(self):
        self._ensure_ready()

    def _ensure_ready(self):
        if self._calibrations is None:
            camera_names = list(self.config.camera_mapping.values())
            self._calibrations = self._calibration_loader(
                str(self.config.calib_json),
                camera_names,
                self.config.extrinsic_source,
                self.config.extrinsic_convention,
            )
        if self._inferencer is None:
            self._inferencer = load_mmpose_inferencer(
                self.config.pose2d,
                self.config.device,
            )


def parse_camera_mapping(items: tuple[str, ...] | list[str]) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for item in items:
        if "=" not in item:
            raise ValueError(
                "camera mapping must use device_id=CalibrationCameraName format"
            )
        device_id, camera_name = item.split("=", 1)
        device_id = device_id.strip()
        camera_name = camera_name.strip()
        if not device_id or not camera_name:
            raise ValueError(
                "camera mapping must include both device ID and calibration name"
            )
        mapping[device_id] = camera_name
    return mapping


def load_mmpose_inferencer(pose2d: str, device: str):
    try:
        from mmpose.apis import MMPoseInferencer
    except Exception as exc:
        raise RuntimeError(
            "MMPose is not installed in the active runtime. "
            "Install the MMPose environment before enabling this processor."
        ) from exc

    try:
        return MMPoseInferencer(pose2d=pose2d, device=device)
    except TypeError:
        return MMPoseInferencer(pose2d, device=device)


def write_frame_set_images(
    processing_input: MotionCaptureInput,
    camera_mapping: dict[str, str],
    output_dir: Path,
) -> dict[str, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    frame_paths: dict[str, Path] = {}
    missing = sorted(set(camera_mapping) - set(processing_input.frames))
    if missing:
        raise ValueError(f"frame set is missing required cameras: {missing}")

    for device_id, camera_name in camera_mapping.items():
        frame = processing_input.frames[device_id]
        suffix = _suffix_from_content_type(frame.content_type)
        path = output_dir / f"{processing_input.frame_set_id}_{device_id}{suffix}"
        path.write_bytes(frame.image_bytes)
        frame_paths[camera_name] = path
    return frame_paths


def triangulate_frame_set(
    processing_input: MotionCaptureInput,
    frame_paths: dict[str, Path],
    camera_mapping: dict[str, str],
    camera_names: list[str],
    calibs: dict[str, CameraCalibration],
    inferencer: Any,
    kpt_thr: float,
    max_reproj_error: float,
    images_undistorted: bool,
) -> TriangulatedFrameResult:
    np = _np()
    kpts_by_cam = {}
    scores_by_cam = {}

    for camera_name in camera_names:
        kpts, scores = run_pose_inference(inferencer, frame_paths[camera_name])
        kpts_by_cam[camera_name] = undistort_keypoints_pixel(
            kpts=kpts,
            K=calibs[camera_name].K,
            dist=calibs[camera_name].dist,
            already_undistorted=images_undistorted,
        )
        scores_by_cam[camera_name] = scores

    joints_world = np.full((17, 3), np.nan, dtype=np.float64)
    joint_scores = np.zeros((17,), dtype=np.float64)
    joint_reproj = np.full((17,), np.inf, dtype=np.float64)
    joint_reproj_by_cam = [{} for _ in range(17)]
    joint_used_cams = [[] for _ in range(17)]
    valid = np.zeros((17,), dtype=bool)

    for joint_index in range(17):
        obs = []
        for camera_name in camera_names:
            xy = kpts_by_cam[camera_name][joint_index]
            score = float(scores_by_cam[camera_name][joint_index])
            if score < kpt_thr or not np.all(np.isfinite(xy)):
                continue
            obs.append((camera_name, calibs[camera_name].P, xy, score))

        X, score, reproj_dict, used_cams = triangulate_joint_best(
            obs,
            max_reproj_error=max_reproj_error,
        )
        if X is None or not np.all(np.isfinite(X)):
            continue

        joints_world[joint_index] = X
        joint_scores[joint_index] = score
        joint_reproj_by_cam[joint_index] = reproj_dict
        joint_used_cams[joint_index] = used_cams
        if reproj_dict:
            joint_reproj[joint_index] = float(np.mean(list(reproj_dict.values())))
        valid[joint_index] = True

    joints_camera = {}
    for camera_name in camera_names:
        arr = np.full((17, 3), np.nan, dtype=np.float64)
        for joint_index in range(17):
            if valid[joint_index]:
                arr[joint_index] = calibs[camera_name].world_to_camera(
                    joints_world[joint_index]
                )
        joints_camera[camera_name] = arr

    valid_reproj = joint_reproj[np.isfinite(joint_reproj)]
    avg_reproj = float(np.mean(valid_reproj)) if len(valid_reproj) else None

    source_frames = {}
    for device_id, camera_name in camera_mapping.items():
        frame = processing_input.frames[device_id]
        source_frames[camera_name] = {
            "device_id": device_id,
            "timestamp_ms": frame.timestamp_ms,
            "sequence": frame.sequence,
            "source_file_path": frame.source_file_path,
            "source_frame_id": frame.source_frame_id,
        }

    return TriangulatedFrameResult(
        frame_set_id=processing_input.frame_set_id,
        anchor_timestamp_ms=processing_input.anchor_timestamp_ms,
        max_delta_ms=processing_input.max_delta_ms,
        num_valid_joints=int(valid.sum()),
        avg_reproj_error_px=avg_reproj,
        joints_world={
            COCO17_NAMES[j]: {
                "xyz": joints_world[j].tolist() if valid[j] else None,
                "score": float(joint_scores[j]),
                "reproj_error_px": (
                    None
                    if not np.isfinite(joint_reproj[j])
                    else float(joint_reproj[j])
                ),
                "reproj_error_by_camera_px": joint_reproj_by_cam[j],
                "used_cameras": joint_used_cams[j],
            }
            for j in range(17)
        },
        joints_camera={
            camera_name: {
                COCO17_NAMES[j]: (
                    joints_camera[camera_name][j].tolist() if valid[j] else None
                )
                for j in range(17)
            }
            for camera_name in camera_names
        },
        camera_centers_world={
            camera_name: calibs[camera_name].center_world.tolist()
            for camera_name in camera_names
        },
        source_frames=source_frames,
    )


def run_pose_inference(inferencer: Any, image_path: Path):
    gen = inferencer(str(image_path), show=False, return_vis=False)
    result = next(gen)
    return extract_best_person(result, num_joints=17)


def extract_best_person(result: dict[str, Any], num_joints: int = 17):
    np = _np()
    preds = result.get("predictions", result)
    instances = flatten_predictions(preds)

    if not instances:
        return (
            np.full((num_joints, 2), np.nan, dtype=np.float64),
            np.zeros((num_joints,), dtype=np.float64),
        )

    best = max(instances, key=instance_score)
    kpts = np.asarray(best.get("keypoints", []), dtype=np.float64)
    if kpts.ndim != 2:
        return (
            np.full((num_joints, 2), np.nan, dtype=np.float64),
            np.zeros((num_joints,), dtype=np.float64),
        )

    if kpts.shape[1] >= 3:
        xy = kpts[:, :2]
        scores_from_kpts = kpts[:, 2]
    else:
        xy = kpts[:, :2]
        scores_from_kpts = None

    scores = best.get("keypoint_scores", best.get("keypoint_score", None))
    if scores is None:
        scores = scores_from_kpts if scores_from_kpts is not None else np.ones(
            (xy.shape[0],),
            dtype=np.float64,
        )
    scores = np.asarray(scores, dtype=np.float64).reshape(-1)

    out_xy = np.full((num_joints, 2), np.nan, dtype=np.float64)
    out_scores = np.zeros((num_joints,), dtype=np.float64)
    count = min(num_joints, xy.shape[0], scores.shape[0])
    out_xy[:count] = xy[:count]
    out_scores[:count] = scores[:count]
    return out_xy, out_scores


def flatten_predictions(preds):
    out = []

    def rec(value):
        if isinstance(value, dict):
            if (
                "keypoints" in value
                or "keypoint_scores" in value
                or "keypoint_score" in value
            ):
                out.append(value)
            else:
                for nested in value.values():
                    rec(nested)
        elif isinstance(value, list):
            for nested in value:
                rec(nested)

    rec(preds)
    return out


def instance_score(instance: dict[str, Any]) -> float:
    np = _np()
    scores = instance.get("keypoint_scores", instance.get("keypoint_score", None))
    if scores is None:
        kpts = np.asarray(instance.get("keypoints", []), dtype=np.float64)
        if kpts.ndim == 2 and kpts.shape[1] >= 3:
            scores = kpts[:, 2]
        else:
            return 0.0

    scores = np.asarray(scores, dtype=np.float64).reshape(-1)
    if len(scores) == 0:
        return 0.0

    bbox_bonus = 0.0
    bbox = instance.get("bbox")
    if bbox is not None:
        box = np.asarray(bbox, dtype=np.float64).reshape(-1)
        if len(box) >= 4:
            if box[2] > box[0] and box[3] > box[1]:
                area = max(0.0, (box[2] - box[0]) * (box[3] - box[1]))
            else:
                area = max(0.0, box[2] * box[3])
            bbox_bonus = min(0.2, math.log1p(area) / 100.0)

    return float(np.nanmean(scores)) + bbox_bonus


def undistort_keypoints_pixel(kpts, K, dist, already_undistorted: bool):
    np = _np()
    if already_undistorted:
        return kpts.copy()
    if dist is None or len(dist.reshape(-1)) == 0 or np.allclose(dist, 0):
        return kpts.copy()

    try:
        import cv2
    except Exception as exc:
        raise RuntimeError(
            "OpenCV is required to undistort keypoints with non-zero distortion."
        ) from exc

    out = kpts.copy()
    valid = np.isfinite(kpts[:, 0]) & np.isfinite(kpts[:, 1])
    if valid.sum() == 0:
        return out

    pts = kpts[valid].reshape(-1, 1, 2).astype(np.float64)
    pts_ud = cv2.undistortPoints(pts, K, dist.reshape(-1), P=K)
    out[valid] = pts_ud.reshape(-1, 2)
    return out


def triangulate_joint_best(
    joint_obs: list[tuple[str, Any, Any, float]],
    max_reproj_error: float,
):
    np = _np()
    if len(joint_obs) < 2:
        return None, 0.0, {}, []

    candidates = []
    X = triangulate_dlt([(P, xy) for _cam, P, xy, _score in joint_obs])
    if X is not None:
        obs = [(cam, P, xy) for cam, P, xy, _score in joint_obs]
        mean_err, errs = reprojection_error(X, obs)
        mean_score = float(np.mean([score for _cam, _P, _xy, score in joint_obs]))
        candidates.append((
            X,
            mean_err,
            errs,
            [cam for cam, _P, _xy, _score in joint_obs],
            mean_score,
        ))

    for pair in combinations(joint_obs, 2):
        X2 = triangulate_dlt([(P, xy) for _cam, P, xy, _score in pair])
        if X2 is None:
            continue
        obs2 = [(cam, P, xy) for cam, P, xy, _score in pair]
        mean_err2, errs2 = reprojection_error(X2, obs2)
        mean_score2 = float(np.mean([score for _cam, _P, _xy, score in pair]))
        candidates.append((
            X2,
            mean_err2,
            errs2,
            [cam for cam, _P, _xy, _score in pair],
            mean_score2,
        ))

    if not candidates:
        return None, 0.0, {}, []

    good = [candidate for candidate in candidates if candidate[1] <= max_reproj_error]
    if good:
        good.sort(key=lambda candidate: (-len(candidate[3]), candidate[1]))
        best = good[0]
    else:
        candidates.sort(key=lambda candidate: candidate[1])
        best = candidates[0]

    X_best, _err_best, errs_best, used_cams, score_best = best
    return X_best, score_best, errs_best, used_cams


def triangulate_dlt(obs: list[tuple[Any, Any]]):
    np = _np()
    if len(obs) < 2:
        return None

    rows = []
    for P, xy in obs:
        x, y = float(xy[0]), float(xy[1])
        rows.append(x * P[2, :] - P[0, :])
        rows.append(y * P[2, :] - P[1, :])

    A = np.asarray(rows, dtype=np.float64)
    try:
        _, _, Vt = np.linalg.svd(A)
    except np.linalg.LinAlgError:
        return None

    Xh = Vt[-1]
    if abs(Xh[3]) < 1e-12:
        return None

    X = Xh[:3] / Xh[3]
    if not np.all(np.isfinite(X)):
        return None
    return X.astype(np.float64)


def project_point(P, X):
    np = _np()
    Xh = np.array([X[0], X[1], X[2], 1.0], dtype=np.float64)
    q = P @ Xh
    if abs(q[2]) < 1e-12:
        return None
    return np.array([q[0] / q[2], q[1] / q[2]], dtype=np.float64)


def reprojection_error(X, obs: list[tuple[str, Any, Any]]):
    np = _np()
    errors = {}
    for camera_name, P, xy in obs:
        uv = project_point(P, X)
        if uv is None:
            errors[camera_name] = float("inf")
        else:
            errors[camera_name] = float(np.linalg.norm(uv - xy.reshape(2)))
    mean_err = float(np.mean(list(errors.values()))) if errors else float("inf")
    return mean_err, errors


def load_calibrations(
    calib_json: str,
    camera_names: list[str],
    extrinsic_source: str,
    extrinsic_convention: str,
) -> dict[str, CameraCalibration]:
    with open(calib_json, "r", encoding="utf-8") as fh:
        data = json.load(fh)

    calibs = {}
    for camera_name in camera_names:
        node = find_camera_node(data, camera_name)
        extrinsic_block = pick_extrinsic_block(node, extrinsic_source)
        K, dist, width, height = parse_intrinsics(camera_name, node, extrinsic_block)
        R, t = parse_extrinsics(
            camera_name,
            node,
            extrinsic_block,
            extrinsic_convention,
        )
        calibs[camera_name] = CameraCalibration(
            name=camera_name,
            K=K,
            dist=dist,
            R=R,
            t=t,
            width=width,
            height=height,
        )
    return calibs


def find_camera_node(data: dict[str, Any], camera_name: str) -> dict[str, Any]:
    target = normalize_name(camera_name)

    def match_node(value: Any) -> bool:
        if not isinstance(value, dict):
            return False
        for key in ["camera", "name", "camera_name", "id"]:
            if key in value and normalize_name(value[key]) == target:
                return True
        return False

    cameras = data.get("cameras")
    if isinstance(cameras, dict):
        for key, value in cameras.items():
            if normalize_name(key) == target and isinstance(value, dict):
                return value
            if match_node(value):
                return value

    if isinstance(cameras, list):
        for value in cameras:
            if match_node(value):
                return value

    base_cameras = get_nested(data, ["base_summary", "cameras"])
    if isinstance(base_cameras, list):
        for value in base_cameras:
            if match_node(value):
                return value

    for key, value in data.items():
        if normalize_name(key) == target and isinstance(value, dict):
            return value
        if match_node(value):
            return value

    raise KeyError(f"camera node not found in calibration JSON: {camera_name}")


def parse_intrinsics(camera_name: str, camera_node: dict[str, Any], extrinsic_block):
    np = _np()
    intrinsic_sources = []
    if isinstance(extrinsic_block, dict):
        intrinsic_sources.append(extrinsic_block)
    intrinsic_sources.append(pick_intrinsic_block(camera_node))
    intrinsic_sources.append(camera_node)

    K = None
    for source in intrinsic_sources:
        if not isinstance(source, dict):
            continue
        direct_intrinsics = source.get("intrinsics")
        if direct_intrinsics is not None and not isinstance(direct_intrinsics, dict):
            arr = np.asarray(direct_intrinsics, dtype=np.float64)
            if arr.size == 9:
                K = arr.reshape(3, 3)
                break
        K = find_matrix_recursive(
            source,
            ["K", "camera_matrix", "cameraMatrix", "intrinsic_matrix", "mtx"],
            (3, 3),
        )
        if K is not None:
            break

    if K is None:
        intr = pick_intrinsic_block(camera_node)
        fx = find_first_key(intr, ["fx", "focal_x"])
        fy = find_first_key(intr, ["fy", "focal_y"])
        cx = find_first_key(intr, ["cx", "principal_x"])
        cy = find_first_key(intr, ["cy", "principal_y"])
        if fx is None or fy is None or cx is None or cy is None:
            raise KeyError(f"{camera_name}: missing camera intrinsics")
        K = np.array(
            [
                [float(fx), 0.0, float(cx)],
                [0.0, float(fy), float(cy)],
                [0.0, 0.0, 1.0],
            ],
            dtype=np.float64,
        )

    dist = None
    for source in intrinsic_sources:
        dist = find_vector_recursive(
            source,
            [
                "distCoef",
                "dist_coeffs",
                "distCoeffs",
                "distortion_coefficients",
                "distortion",
                "D",
                "dist",
            ],
            min_len=1,
        )
        if dist is not None:
            break
    if dist is None:
        dist = np.zeros(5, dtype=np.float64)
    else:
        dist = dist.astype(np.float64).reshape(-1)

    width, height = parse_image_size(camera_node)
    return K.astype(np.float64), dist, width, height


def parse_extrinsics(
    camera_name: str,
    camera_node: dict[str, Any],
    block: dict[str, Any],
    convention: str,
):
    np = _np()
    T = find_matrix_recursive(
        block,
        [
            "T_world_to_camera",
            "Tcw",
            "T_cw",
            "world_to_camera",
            "T_w2c",
            "w2c",
            "extrinsic_matrix",
            "extrinsic",
            "T",
            "T_camera_to_world",
            "Twc",
            "T_wc",
            "camera_to_world",
            "c2w",
        ],
        (4, 4),
    )
    if T is not None:
        if convention == "world_to_camera":
            return T[:3, :3].astype(np.float64), T[:3, 3].astype(np.float64)
        R_wc = T[:3, :3]
        C_w = T[:3, 3]
        R_cw = R_wc.T
        return R_cw.astype(np.float64), (-R_cw @ C_w).astype(np.float64)

    R = find_matrix_recursive(
        block,
        [
            "R",
            "rotation",
            "rotation_matrix",
            "rot",
            "R_cw",
            "Rcw",
            "R_world_to_camera",
            "rotation_world_to_camera",
        ],
        (3, 3),
    )
    t = find_vector_recursive(
        block,
        ["t", "translation", "trans", "t_cw", "tcw", "t_world_to_camera"],
        min_len=3,
    )
    C = find_vector_recursive(
        block,
        [
            "C",
            "center",
            "camera_center",
            "cameraCenter",
            "position",
            "camera_position",
            "camera_center_world",
        ],
        min_len=3,
    )

    if R is None:
        raise KeyError(f"{camera_name}: missing camera extrinsic rotation")

    R = R.astype(np.float64)
    if t is not None:
        t = t[:3].astype(np.float64)
        if convention == "world_to_camera":
            return R, t
        R_wc = R
        C_w = t
        R_cw = R_wc.T
        return R_cw.astype(np.float64), (-R_cw @ C_w).astype(np.float64)

    if C is not None:
        C = C[:3].astype(np.float64)
        if convention == "world_to_camera":
            return R, (-R @ C).astype(np.float64)
        R_cw = R.T
        return R_cw.astype(np.float64), (-R_cw @ C).astype(np.float64)

    raise KeyError(f"{camera_name}: missing camera extrinsic translation/center")


def pick_intrinsic_block(camera_node: dict[str, Any]) -> dict[str, Any]:
    for key in ["intrinsics", "intrinsic", "camera_intrinsics", "iop"]:
        value = camera_node.get(key)
        if isinstance(value, dict):
            return value
    return camera_node


def pick_extrinsic_block(camera_node: dict[str, Any], source: str) -> dict[str, Any]:
    if source == "root":
        return camera_node

    aliases = {
        "bundleadjusted": [
            "bundleadjusted",
            "bundle_adjusted",
            "bundleAdjusted",
            "bundle",
            "ba",
            "optimized",
            "extrinsics_bundleadjusted",
            "bundle_adjusted_extrinsics",
        ],
        "bundle_adjusted": [
            "bundle_adjusted",
            "bundleadjusted",
            "bundleAdjusted",
            "bundle",
            "ba",
            "optimized",
            "extrinsics_bundleadjusted",
            "bundle_adjusted_extrinsics",
        ],
        "prebundle": [
            "prebundle",
            "pre_bundle",
            "before_bundle",
            "initial",
            "extrinsics_prebundle",
            "prebundle_extrinsics",
        ],
        "extrinsics": ["extrinsics", "extrinsic", "external", "eop"],
    }
    if source != "auto":
        for key in aliases.get(source, [source]):
            if isinstance(camera_node.get(key), dict):
                return camera_node[key]
        return camera_node

    for group in [aliases["bundleadjusted"], aliases["extrinsics"], aliases["prebundle"]]:
        for key in group:
            if isinstance(camera_node.get(key), dict):
                return camera_node[key]
    return camera_node


def find_matrix_recursive(value: Any, candidate_keys: list[str], shape: tuple[int, int]):
    np = _np()
    if isinstance(value, dict):
        item = find_first_key(value, candidate_keys)
        if item is not None:
            arr = np.asarray(item, dtype=np.float64)
            if arr.shape == shape:
                return arr
            if arr.size == shape[0] * shape[1]:
                return arr.reshape(shape)
        for nested in value.values():
            found = find_matrix_recursive(nested, candidate_keys, shape)
            if found is not None:
                return found
    return None


def find_vector_recursive(value: Any, candidate_keys: list[str], min_len: int = 3):
    np = _np()
    if isinstance(value, dict):
        item = find_first_key(value, candidate_keys)
        if item is not None:
            arr = np.asarray(item, dtype=np.float64).reshape(-1)
            if len(arr) >= min_len:
                return arr
        for nested in value.values():
            found = find_vector_recursive(nested, candidate_keys, min_len)
            if found is not None:
                return found
    return None


def find_first_key(value: dict[str, Any], keys: list[str]):
    if not isinstance(value, dict):
        return None
    for key in keys:
        if key in value:
            return value[key]
    normalized = {normalize_name(key): key for key in value}
    for key in keys:
        normalized_key = normalize_name(key)
        if normalized_key in normalized:
            return value[normalized[normalized_key]]
    return None


def parse_image_size(camera_node: dict[str, Any]) -> tuple[int | None, int | None]:
    image_size = find_first_key(camera_node, ["image_size", "imageSize", "resolution", "size"])
    width = None
    height = None
    if isinstance(image_size, dict):
        width = image_size.get("width", image_size.get("w"))
        height = image_size.get("height", image_size.get("h"))
    elif isinstance(image_size, (list, tuple)) and len(image_size) >= 2:
        width = image_size[0]
        height = image_size[1]

    if width is None:
        width = find_first_key(camera_node, ["width", "image_width", "w"])
    if height is None:
        height = find_first_key(camera_node, ["height", "image_height", "h"])

    return (
        int(width) if width is not None else None,
        int(height) if height is not None else None,
    )


def get_nested(value: dict[str, Any], keys: list[str]):
    current = value
    for key in keys:
        if not isinstance(current, dict) or key not in current:
            return None
        current = current[key]
    return current


def normalize_name(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value).lower())


def _suffix_from_content_type(content_type: str) -> str:
    normalized = content_type.lower().split(";", 1)[0].strip()
    if normalized == "image/png":
        return ".png"
    if normalized in {"image/jpg", "image/jpeg"}:
        return ".jpg"
    if normalized == "image/webp":
        return ".webp"
    return ".img"


def _np():
    try:
        import numpy as np
    except Exception as exc:
        raise RuntimeError(
            "numpy is required for MMPose triangulation processing."
        ) from exc
    return np
