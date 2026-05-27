
#!/usr/bin/env python3
from __future__ import annotations

"""
CasCalib wrapper that can optionally run MMPose automatically when CameraN.json
files are missing.

Main idea
---------
1) Prepare raw per-camera sequences for CasCalib. In this variant, ChArUco intrinsics are disabled by default and images are copied without undistortion.
2) If MMPose JSON is missing and auto-mode is enabled, run MMPose on each
   prepared camera folder and write Camera1.json, Camera2.json, ...
3) Run CasCalib single-view initialization per camera.
4) Run CasCalib temporal sync + ICP + bundle adjustment.
5) Save both pre-bundle and bundle-adjusted camera parameters.
"""

import argparse
import copy
import csv
import inspect
import json
import os
import re
import sys
from collections import OrderedDict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

os.environ.setdefault("MPLBACKEND", "Agg")

import matplotlib.image as mpimg
import numpy as np


# -----------------------------------------------------------------------------
# IO helpers
# -----------------------------------------------------------------------------

def read_csv_rows(path: Path) -> List[dict]:
    with open(path, "r", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def load_frame_map(meta_root: Path, cam_idx: int) -> Tuple[Dict[int, float], Dict[int, str]]:
    rows = read_csv_rows(meta_root / f"Camera{cam_idx}_frame_map.csv")
    idx_to_ts = {int(r["seq_index"]): float(r["timestamp_sec"]) for r in rows}
    idx_to_name = {int(r["seq_index"]): r["out_name"] for r in rows}
    return idx_to_ts, idx_to_name


def load_meta(meta_root: Path, cam_idx: int) -> dict:
    return json.loads((meta_root / f"Camera{cam_idx}_meta.json").read_text(encoding="utf-8"))


def save_json(path: Path, obj: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, ensure_ascii=False), encoding="utf-8")


# -----------------------------------------------------------------------------
# Dict / timestamp helpers
# -----------------------------------------------------------------------------

def normalize_key_dict(d: Mapping) -> OrderedDict:
    out = OrderedDict()
    for k, v in d.items():
        try:
            out[int(k)] = v
        except Exception:
            out[k] = v
    return OrderedDict(sorted(out.items(), key=lambda kv: kv[0]))


def filter_dict_by_start_ts(d: Mapping, idx_to_ts: Dict[int, float], start_ts: float) -> OrderedDict:
    out = OrderedDict()
    for k, v in d.items():
        ik = int(k)
        if ik in idx_to_ts and idx_to_ts[ik] >= start_ts:
            out[ik] = v
    return out


def first_valid_ts_from_data(data_2d: Mapping, idx_to_ts: Dict[int, float]) -> float:
    keys = sorted(int(k) for k in data_2d.keys() if int(k) in idx_to_ts)
    if not keys:
        raise RuntimeError("No valid person detections found for one of the cameras.")
    return idx_to_ts[keys[0]]


def median_frame_dt(idx_to_ts: Dict[int, float]) -> float:
    xs = [idx_to_ts[k] for k in sorted(idx_to_ts.keys())]
    if len(xs) < 2:
        return 0.0
    diffs = np.diff(xs)
    diffs = diffs[diffs > 0]
    return float(np.median(diffs)) if len(diffs) else 0.0


def choose_representative_index(
    idx_to_ts: Dict[int, float],
    valid_indices: Iterable[int],
    start_ts: float,
) -> int:
    candidate_indices = sorted(
        idx for idx in set(int(i) for i in valid_indices)
        if idx in idx_to_ts and idx_to_ts[idx] >= start_ts
    )
    if not candidate_indices:
        raise RuntimeError("No representative frame candidate exists after common start timestamp.")

    candidate_ts = np.array([idx_to_ts[idx] for idx in candidate_indices], dtype=np.float64)
    target_ts = float(np.median(candidate_ts))
    best_idx = min(candidate_indices, key=lambda idx: abs(idx_to_ts[idx] - target_ts))
    return int(best_idx)


def representative_frame_from_valid_indices(
    undist_dir: Path,
    idx_to_ts: Dict[int, float],
    valid_indices: Iterable[int],
    start_ts: float,
) -> Tuple[int, Path]:
    rep_idx = choose_representative_index(idx_to_ts, valid_indices, start_ts)
    rep_path = undist_dir / f"{rep_idx:08d}.jpg"
    if not rep_path.exists():
        raise FileNotFoundError(f"Representative frame not found: {rep_path}")
    return rep_idx, rep_path


# -----------------------------------------------------------------------------
# Geometry helpers
# -----------------------------------------------------------------------------

def make_projection_matrix(K: np.ndarray, R_world_to_cam: np.ndarray, C_world: np.ndarray) -> np.ndarray:
    t = -R_world_to_cam @ C_world.reshape(3, 1)
    Rt = np.concatenate([R_world_to_cam, t], axis=1)
    return K @ Rt


def focal_mismatch_ratio(cam_matrix_est: np.ndarray, known_K: np.ndarray) -> float:
    fx_est = float(cam_matrix_est[0, 0])
    fy_est = float(cam_matrix_est[1, 1])
    fx_known = float(known_K[0, 0])
    fy_known = float(known_K[1, 1])
    return max(
        abs(fx_est - fx_known) / max(abs(fx_known), 1e-9),
        abs(fy_est - fy_known) / max(abs(fy_known), 1e-9),
    )


def select_single_view_K(
    known_K: np.ndarray,
    cam_matrix_est: np.ndarray,
    mismatch_ratio: float,
    policy: str,
    threshold: float,
) -> Tuple[np.ndarray, str]:
    if policy == "known":
        return np.asarray(known_K, dtype=np.float64), "known"
    if policy == "estimated":
        return np.asarray(cam_matrix_est, dtype=np.float64), "estimated"
    if mismatch_ratio > threshold:
        return np.asarray(cam_matrix_est, dtype=np.float64), "estimated_due_to_large_focal_mismatch"
    return np.asarray(known_K, dtype=np.float64), "known"


# -----------------------------------------------------------------------------
# CasCalib loading / validation
# -----------------------------------------------------------------------------

def validate_cascalib_repo(cascalib_repo: Path) -> None:
    required = [
        cascalib_repo / "configuration.json",
        cascalib_repo / "hyperparameter.json",
        cascalib_repo / "run_calibration_ransac.py",
        cascalib_repo / "time_align.py",
        cascalib_repo / "ICP.py",
        cascalib_repo / "bundle_adjustment.py",
        cascalib_repo / "geometry.py",
        cascalib_repo / "multiview_utils.py",
        cascalib_repo / "data.py",
        cascalib_repo / "util.py",
    ]
    missing = [str(p) for p in required if not p.exists()]
    if missing:
        raise FileNotFoundError(
            "CasCalib repo layout looks incomplete. Missing files:\n- " + "\n- ".join(missing)
        )


def load_cascalib_modules(cascalib_repo: Path):
    repo_str = str(cascalib_repo.resolve())
    if repo_str not in sys.path:
        sys.path.insert(0, repo_str)
    try:
        import data  # type: ignore
        import geometry  # type: ignore
        import multiview_utils  # type: ignore
        import time_align  # type: ignore
        import ICP  # type: ignore
        import util  # type: ignore
        import bundle_adjustment  # type: ignore
        from run_calibration_ransac import run_calibration_ransac  # type: ignore
    except ModuleNotFoundError as e:
        if "pytorch3d" in str(e).lower():
            raise ModuleNotFoundError(
                "pytorch3d가 필요합니다. CasCalib README처럼 pytorch3d를 설치한 뒤 다시 실행하세요. "
                "예: pip install 'git+https://github.com/facebookresearch/pytorch3d.git@stable'"
            ) from e
        raise ModuleNotFoundError(
            f"CasCalib import failed from: {cascalib_repo}\nOriginal error: {e}"
        ) from e
    return data, geometry, multiview_utils, time_align, ICP, util, bundle_adjustment, run_calibration_ransac


# -----------------------------------------------------------------------------
# Runtime hyperparameter writing
# -----------------------------------------------------------------------------

def write_runtime_hyperparameter(
    cascalib_repo: Path,
    output_dir: Path,
    person_height_m: float,
) -> Path:
    src = cascalib_repo / "hyperparameter.json"
    with open(src, "r", encoding="utf-8") as f:
        hp = json.load(f)
    hp["h"] = float(person_height_m)
    suffix = str(person_height_m).replace(".", "p")
    runtime_hp = output_dir / f"runtime_hyperparameter_h{suffix}.json"
    with open(runtime_hp, "w", encoding="utf-8") as f:
        json.dump(hp, f, indent=2, ensure_ascii=False)
    return runtime_hp


# -----------------------------------------------------------------------------
# sync_dict guards
# -----------------------------------------------------------------------------

def sanitize_sync_dict_array(sync_dict_array: Sequence[Mapping], expected_len: int) -> List[OrderedDict]:
    clean: List[OrderedDict] = []
    for item in sync_dict_array[:expected_len]:
        od = OrderedDict()
        for k, v in item.items():
            od[int(k)] = int(v)
        clean.append(OrderedDict(sorted(od.items(), key=lambda kv: kv[0])))
    return clean


# -----------------------------------------------------------------------------
# Auto MMPose helpers
# -----------------------------------------------------------------------------

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff", ".webp"}


def _xyxy_from_bbox_like(bbox) -> Optional[List[float]]:
    if bbox is None:
        return None
    arr = np.asarray(bbox, dtype=np.float64).reshape(-1)
    if arr.size >= 4:
        x1, y1, a, b = arr[:4].tolist()
        # prefer xyxy when it already looks valid
        if a >= x1 and b >= y1:
            return [float(x1), float(y1), float(a), float(b)]
        return [float(x1), float(y1), float(x1 + a), float(y1 + b)]
    return None


def _normalize_instance(inst: Mapping, idx: int, img_path: Path, bbox_thr: float) -> Optional[dict]:
    bbox = (
        inst.get("bbox")
        or inst.get("bbox_xyxy")
        or inst.get("bbox_xywh")
        or inst.get("bboxes")
    )
    bbox_xyxy = _xyxy_from_bbox_like(bbox)
    bbox_score = float(inst.get("bbox_score", inst.get("score", 1.0)))
    if bbox_score < bbox_thr:
        return None

    kpts = inst.get("keypoints")
    if kpts is None:
        return None
    kpts = np.asarray(kpts, dtype=np.float64)
    if kpts.ndim == 1 and kpts.size % 2 == 0:
        kpts = kpts.reshape(-1, 2)
    elif kpts.ndim == 2 and kpts.shape[1] >= 2:
        kpts = kpts[:, :2]
    else:
        return None

    scores = inst.get("keypoint_scores")
    if scores is None and "keypoints_visible" in inst:
        scores = inst["keypoints_visible"]
    if scores is None and isinstance(inst.get("score"), (float, int)):
        scores = [float(inst["score"])] * int(kpts.shape[0])
    if scores is None:
        scores = np.ones((kpts.shape[0],), dtype=np.float64)
    scores = np.asarray(scores, dtype=np.float64).reshape(-1)
    if scores.size < kpts.shape[0]:
        pad = np.ones((kpts.shape[0] - scores.size,), dtype=np.float64)
        scores = np.concatenate([scores, pad], axis=0)
    scores = scores[: kpts.shape[0]]

    coco_keypoints = []
    for (x, y), s in zip(kpts, scores):
        coco_keypoints.extend([float(x), float(y), float(s)])

    return {
        "frame_id": int(idx),
        "img_id": int(idx),
        "image_id": int(idx),
        "image_path": str(img_path),
        "category_id": 1,
        "bbox": bbox_xyxy if bbox_xyxy is not None else [0.0, 0.0, 0.0, 0.0],
        "bbox_score": float(bbox_score),
        "score": float(bbox_score),
        "keypoints": [[float(x), float(y)] for x, y in kpts.tolist()],
        "keypoint_scores": [float(v) for v in scores.tolist()],
        "coco_keypoints": coco_keypoints,
    }


def _iter_image_paths(camera_dir: Path) -> List[Path]:
    return [p for p in sorted(camera_dir.iterdir()) if p.is_file() and p.suffix.lower() in IMAGE_EXTS]


# COCO-17 skeleton. If a model returns fewer keypoints, unavailable edges are skipped.
COCO17_SKELETON = [
    (5, 7), (7, 9),          # left arm
    (6, 8), (8, 10),         # right arm
    (5, 6),                  # shoulders
    (5, 11), (6, 12),        # torso
    (11, 12),                # hips
    (11, 13), (13, 15),      # left leg
    (12, 14), (14, 16),      # right leg
    (0, 1), (0, 2), (1, 3), (2, 4),  # head
]


def _parse_keypoints_for_vis(inst: Mapping) -> Tuple[Optional[np.ndarray], Optional[np.ndarray]]:
    kpts_raw = inst.get("keypoints")
    if kpts_raw is None:
        kpts_raw = inst.get("coco_keypoints")
    if kpts_raw is None:
        return None, None

    try:
        arr = np.asarray(kpts_raw, dtype=np.float64)
    except Exception:
        return None, None

    scores_from_keypoints = None
    if arr.ndim == 1:
        if arr.size % 3 == 0:
            arr = arr.reshape(-1, 3)
            scores_from_keypoints = arr[:, 2]
            kpts = arr[:, :2]
        elif arr.size % 2 == 0:
            kpts = arr.reshape(-1, 2)
        else:
            return None, None
    elif arr.ndim == 2 and arr.shape[1] >= 3:
        scores_from_keypoints = arr[:, 2]
        kpts = arr[:, :2]
    elif arr.ndim == 2 and arr.shape[1] >= 2:
        kpts = arr[:, :2]
    else:
        return None, None

    scores_raw = inst.get("keypoint_scores")
    if scores_raw is None:
        scores_raw = inst.get("keypoints_visible")
    if scores_raw is None:
        scores = scores_from_keypoints
    else:
        try:
            scores = np.asarray(scores_raw, dtype=np.float64).reshape(-1)
        except Exception:
            scores = scores_from_keypoints

    if scores is None:
        scores = np.ones((kpts.shape[0],), dtype=np.float64)
    if scores.size < kpts.shape[0]:
        scores = np.concatenate([scores, np.zeros((kpts.shape[0] - scores.size,), dtype=np.float64)])
    return kpts.astype(np.float64), scores[: kpts.shape[0]].astype(np.float64)


def _normalize_vis_instance(inst: Mapping, fallback_frame_id: int, fallback_image_path: Optional[Path]) -> Optional[dict]:
    kpts, scores = _parse_keypoints_for_vis(inst)
    if kpts is None or scores is None:
        return None

    bbox = inst.get("bbox") or inst.get("bbox_xyxy") or inst.get("bbox_xywh") or inst.get("bboxes")
    bbox_xyxy = _xyxy_from_bbox_like(bbox)
    try:
        bbox_score = float(inst.get("bbox_score", inst.get("score", 1.0)))
    except Exception:
        bbox_score = 1.0

    image_path = inst.get("image_path")
    if not image_path and fallback_image_path is not None:
        image_path = str(fallback_image_path)

    try:
        frame_id = int(inst.get("frame_id", inst.get("img_id", inst.get("image_id", fallback_frame_id))))
    except Exception:
        frame_id = int(fallback_frame_id)

    return {
        "frame_id": frame_id,
        "image_path": str(image_path) if image_path else "",
        "bbox": bbox_xyxy if bbox_xyxy is not None else [0.0, 0.0, 0.0, 0.0],
        "bbox_score": bbox_score,
        "keypoints": [[float(x), float(y)] for x, y in kpts.tolist()],
        "keypoint_scores": [float(v) for v in scores.tolist()],
    }


def _should_save_mmpose_vis(seq_idx: int, saved_count: int, args) -> bool:
    every = max(1, int(getattr(args, "mmpose_vis_every", 1)))
    max_per_camera = int(getattr(args, "mmpose_vis_max_per_camera", 0))
    if seq_idx % every != 0:
        return False
    if max_per_camera > 0 and saved_count >= max_per_camera:
        return False
    return True


def _get_mmpose_vis_root(args, mmpose_dir: Path) -> Optional[Path]:
    if not bool(getattr(args, "save_mmpose_vis", False)):
        return None
    vis_dir = getattr(args, "mmpose_vis_dir", None)
    if vis_dir:
        return Path(vis_dir)
    return mmpose_dir.parent / "mmpose_vis"


def _valid_kpt(kpts: np.ndarray, scores: np.ndarray, idx: int, kpt_thr: float, width: int, height: int) -> bool:
    if idx < 0 or idx >= len(kpts):
        return False
    x, y = kpts[idx]
    if not np.isfinite(x) or not np.isfinite(y):
        return False
    if scores[idx] < kpt_thr:
        return False
    # Keep this permissive: detector outputs can be slightly outside the image border.
    if x < -width * 0.1 or y < -height * 0.1 or x > width * 1.1 or y > height * 1.1:
        return False
    return True


def _save_mmpose_visualization(
    img_path: Path,
    frame_instances: Sequence[Mapping],
    out_path: Path,
    kpt_thr: float,
) -> bool:
    try:
        import cv2  # type: ignore
    except Exception as e:
        raise RuntimeError("MMPose visualization requires cv2/opencv.") from e

    img = cv2.imread(str(img_path), cv2.IMREAD_COLOR)
    if img is None:
        return False

    height, width = img.shape[:2]
    line_color = (0, 255, 255)
    point_color = (0, 0, 255)
    bbox_color = (0, 255, 0)
    text_color = (255, 255, 255)

    for person_idx, inst in enumerate(frame_instances):
        kpts = np.asarray(inst.get("keypoints", []), dtype=np.float64)
        scores = np.asarray(inst.get("keypoint_scores", []), dtype=np.float64).reshape(-1)
        if kpts.ndim != 2 or kpts.shape[1] < 2:
            continue
        kpts = kpts[:, :2]
        if scores.size < len(kpts):
            scores = np.concatenate([scores, np.ones((len(kpts) - scores.size,), dtype=np.float64)])
        scores = scores[: len(kpts)]

        bbox = _xyxy_from_bbox_like(inst.get("bbox"))
        if bbox is not None:
            x1, y1, x2, y2 = [int(round(v)) for v in bbox]
            if x2 > x1 and y2 > y1:
                cv2.rectangle(img, (x1, y1), (x2, y2), bbox_color, 2)
                label = f"person {person_idx} {float(inst.get('bbox_score', 1.0)):.2f}"
                cv2.putText(img, label, (x1, max(0, y1 - 6)), cv2.FONT_HERSHEY_SIMPLEX, 0.5, bbox_color, 1, cv2.LINE_AA)

        for a, b in COCO17_SKELETON:
            if _valid_kpt(kpts, scores, a, kpt_thr, width, height) and _valid_kpt(kpts, scores, b, kpt_thr, width, height):
                pa = tuple(np.round(kpts[a]).astype(int).tolist())
                pb = tuple(np.round(kpts[b]).astype(int).tolist())
                cv2.line(img, pa, pb, line_color, 2, cv2.LINE_AA)

        for kp_idx, (x, y) in enumerate(kpts):
            if _valid_kpt(kpts, scores, kp_idx, kpt_thr, width, height):
                cv2.circle(img, (int(round(x)), int(round(y))), 3, point_color, -1, cv2.LINE_AA)

    cv2.putText(
        img,
        f"MMPose detections: {len(frame_instances)}",
        (12, 24),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.7,
        text_color,
        2,
        cv2.LINE_AA,
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    return bool(cv2.imwrite(str(out_path), img))


def _coerce_existing_mmpose_to_normalized_frames(points_2d: object, cam_dir: Path) -> List[dict]:
    # Native CasCalib format generated by this wrapper:
    # {"Info": [{"frame": int, "track_id": int, "bbox": ..., "keypoints": [[x,y,s], ...]}, ...]}
    if isinstance(points_2d, dict) and isinstance(points_2d.get("Info"), list):
        grouped: Dict[int, dict] = OrderedDict()
        for fallback_id, inst in enumerate(points_2d["Info"]):
            if not isinstance(inst, Mapping):
                continue
            try:
                frame_id = int(inst.get("frame", inst.get("frame_id", fallback_id)))
            except Exception:
                frame_id = int(fallback_id)
            fallback_path = Path(str(inst.get("image_path"))) if inst.get("image_path") else cam_dir / f"{frame_id:08d}.jpg"
            kpts = np.asarray(inst.get("keypoints", []), dtype=np.float64)
            if kpts.ndim != 2 or kpts.shape[1] < 2:
                continue
            scores = kpts[:, 2] if kpts.shape[1] >= 3 else np.ones((kpts.shape[0],), dtype=np.float64)
            norm = {
                "frame_id": frame_id,
                "image_path": str(fallback_path),
                "bbox": inst.get("bbox", [0.0, 0.0, 0.0, 0.0]),
                "bbox_score": float(inst.get("bbox_score", inst.get("score", 1.0))),
                "keypoints": [[float(x), float(y)] for x, y in kpts[:, :2].tolist()],
                "keypoint_scores": [float(v) for v in scores.reshape(-1).tolist()],
            }
            if frame_id not in grouped:
                grouped[frame_id] = {
                    "frame_id": frame_id,
                    "img_id": frame_id,
                    "image_id": frame_id,
                    "image_path": str(fallback_path),
                    "instances": [],
                }
            grouped[frame_id]["instances"].append(norm)
        return list(grouped.values())

    if isinstance(points_2d, dict) and isinstance(points_2d.get("instance_info"), list):
        items = points_2d["instance_info"]
    elif isinstance(points_2d, list):
        items = points_2d
    else:
        return []

    frames: List[dict] = []
    if items and isinstance(items[0], Mapping) and "instances" in items[0]:
        for fallback_id, frame in enumerate(items):
            if not isinstance(frame, Mapping):
                continue
            try:
                frame_id = int(frame.get("frame_id", frame.get("img_id", frame.get("image_id", fallback_id))))
            except Exception:
                frame_id = int(fallback_id)
            fallback_path = Path(str(frame.get("image_path"))) if frame.get("image_path") else cam_dir / f"{frame_id:08d}.jpg"
            insts = []
            for inst in frame.get("instances", []):
                if isinstance(inst, Mapping):
                    norm = _normalize_vis_instance(inst, frame_id, fallback_path)
                    if norm is not None:
                        insts.append(norm)
            frames.append(
                {
                    "frame_id": frame_id,
                    "img_id": frame_id,
                    "image_id": frame_id,
                    "image_path": str(fallback_path),
                    "instances": insts,
                }
            )
        return frames

    grouped: Dict[int, dict] = OrderedDict()
    for fallback_id, inst in enumerate(items):
        if not isinstance(inst, Mapping):
            continue
        try:
            frame_id = int(inst.get("frame_id", inst.get("img_id", inst.get("image_id", fallback_id))))
        except Exception:
            frame_id = int(fallback_id)
        fallback_path = Path(str(inst.get("image_path"))) if inst.get("image_path") else cam_dir / f"{frame_id:08d}.jpg"
        norm = _normalize_vis_instance(inst, frame_id, fallback_path)
        if norm is None:
            continue
        if frame_id not in grouped:
            grouped[frame_id] = {
                "frame_id": frame_id,
                "img_id": frame_id,
                "image_id": frame_id,
                "image_path": str(fallback_path),
                "instances": [],
            }
        grouped[frame_id]["instances"].append(norm)
    return list(grouped.values())

def render_mmpose_visualizations_from_jsons(
    *,
    undist_root: Path,
    mmpose_dir: Path,
    num_cameras: int,
    args,
) -> dict:
    vis_root = _get_mmpose_vis_root(args, mmpose_dir)
    if vis_root is None:
        return {"enabled": False}
    vis_root.mkdir(parents=True, exist_ok=True)

    summary = {"enabled": True, "mmpose_vis_dir": str(vis_root), "cameras": [], "warnings": []}
    kpt_thr = float(getattr(args, "mmpose_vis_kpt_thr", getattr(args, "mmpose_kpt_thr", 0.0)))

    for cam_idx in range(1, num_cameras + 1):
        cam_dir = undist_root / f"Camera{cam_idx}"
        json_path = mmpose_dir / f"Camera{cam_idx}.json"
        vis_cam_dir = vis_root / f"Camera{cam_idx}"
        if not json_path.exists():
            summary["warnings"].append(f"Camera{cam_idx}: missing json for visualization: {json_path}")
            continue

        with open(json_path, "r", encoding="utf-8") as f:
            points_2d = json.load(f)
        frames = _coerce_existing_mmpose_to_normalized_frames(points_2d, cam_dir)

        saved_count = 0
        for frame in frames:
            seq_idx = int(frame.get("frame_id", frame.get("image_id", 0)))
            if not _should_save_mmpose_vis(seq_idx, saved_count, args):
                continue
            img_path = Path(str(frame.get("image_path", "")))
            if not img_path.exists():
                img_path = cam_dir / f"{seq_idx:08d}.jpg"
            if not img_path.exists():
                summary["warnings"].append(f"Camera{cam_idx}: missing image for visualization: {img_path}")
                continue
            out_path = vis_cam_dir / f"{seq_idx:08d}_{img_path.stem}_pose.jpg"
            if _save_mmpose_visualization(img_path, frame.get("instances", []), out_path, kpt_thr):
                saved_count += 1

        summary["cameras"].append(
            {
                "camera": f"Camera{cam_idx}",
                "source_json": str(json_path),
                "visualization_dir": str(vis_cam_dir),
                "frames_in_json": len(frames),
                "saved_visualization_count": saved_count,
            }
        )

    save_json(vis_root / "_mmpose_visualization_summary.json", summary)
    return summary


def _build_candidates(normalized_frames: List[dict]) -> List[Tuple[str, object]]:
    # CasCalib/data.py::coco_mmpose_dataloader expects a top-level
    # dictionary with an "Info" list. Each item must include:
    #   frame, track_id, bbox, keypoints
    # where keypoints is a COCO-17 list of [x, y, confidence].
    # Put this candidate first because it matches the original CasCalib loader.
    cascalib_info = {"Info": []}
    for frame in normalized_frames:
        frame_id = int(frame["frame_id"])
        for person_idx, inst in enumerate(frame["instances"]):
            kpts = inst.get("keypoints", [])
            scores = inst.get("keypoint_scores", [])
            kpts_arr = np.asarray(kpts, dtype=np.float64)
            scores_arr = np.asarray(scores, dtype=np.float64).reshape(-1)
            if kpts_arr.ndim != 2 or kpts_arr.shape[1] < 2:
                continue
            kpts_arr = kpts_arr[:, :2]
            if scores_arr.size < len(kpts_arr):
                scores_arr = np.concatenate(
                    [scores_arr, np.ones((len(kpts_arr) - scores_arr.size,), dtype=np.float64)],
                    axis=0,
                )

            # CasCalib uses the first 17 COCO keypoints. If the MMPose model
            # returns extra keypoints, ignore extras; if fewer than 17, skip.
            if len(kpts_arr) < 17:
                continue

            info_keypoints = []
            for (x, y), sc in zip(kpts_arr[:17], scores_arr[:17]):
                info_keypoints.append([float(x), float(y), float(sc)])

            track_id = inst.get("track_id", person_idx)
            try:
                track_id = int(track_id)
            except Exception:
                track_id = int(person_idx)

            cascalib_info["Info"].append(
                {
                    "frame": int(frame_id),
                    "track_id": int(track_id),
                    "bbox": inst.get("bbox", [0.0, 0.0, 0.0, 0.0]),
                    "keypoints": info_keypoints,
                    "image_path": inst.get("image_path", frame.get("image_path", "")),
                    "bbox_score": float(inst.get("bbox_score", inst.get("score", 1.0))),
                    "score": float(inst.get("score", inst.get("bbox_score", 1.0))),
                }
            )

    # 1) flat COCO-style detection list
    flat_coco = []
    for frame in normalized_frames:
        for inst in frame["instances"]:
            flat_coco.append(
                {
                    "image_id": inst["image_id"],
                    "img_id": inst["img_id"],
                    "category_id": 1,
                    "bbox": inst["bbox"],
                    "score": inst["score"],
                    "bbox_score": inst["bbox_score"],
                    "keypoints": inst["coco_keypoints"],
                    "image_path": inst["image_path"],
                    "frame_id": inst["frame_id"],
                    "keypoint_scores": inst["keypoint_scores"],
                }
            )

    # 2) MMPose-like dict with instance_info as flat instances
    instance_info_flat = {
        "meta_info": {"format": "auto_mmpose_flat_instances"},
        "instance_info": [
            {
                "image_id": inst["image_id"],
                "img_id": inst["img_id"],
                "frame_id": inst["frame_id"],
                "image_path": inst["image_path"],
                "category_id": 1,
                "bbox": inst["bbox"],
                "bbox_score": inst["bbox_score"],
                "keypoints": inst["keypoints"],
                "keypoint_scores": inst["keypoint_scores"],
                "score": inst["score"],
                "coco_keypoints": inst["coco_keypoints"],
            }
            for frame in normalized_frames for inst in frame["instances"]
        ],
    }

    # 3) list of frame dictionaries with instances
    frame_list = [
        {
            "image_id": frame["image_id"],
            "img_id": frame["img_id"],
            "frame_id": frame["frame_id"],
            "image_path": frame["image_path"],
            "instances": frame["instances"],
        }
        for frame in normalized_frames
    ]

    # 4) dict with instance_info per-frame
    instance_info_frames = {
        "meta_info": {"format": "auto_mmpose_frame_instances"},
        "instance_info": frame_list,
    }

    return [
        ("cascalib_info", cascalib_info),
        ("flat_coco", flat_coco),
        ("instance_info_flat", instance_info_flat),
        ("frame_list", frame_list),
        ("instance_info_frames", instance_info_frames),
    ]

def _validate_candidate_with_cascalib(
    data_module,
    multiview_utils_module,
    candidate_obj: object,
    detector_type: int,
    confidence: float,
) -> Tuple[bool, int, str]:
    try:
        if detector_type == 0:
            datastore = data_module.coco_mmpose_dataloader(candidate_obj)
        else:
            datastore = data_module.alphapose_dataloader(candidate_obj)
        d = normalize_key_dict(multiview_utils_module.get_ankles_heads_dictionary(datastore, cond_tol=confidence))
        n = len(d)
        return (n > 0), n, ""
    except Exception as e:
        return False, 0, f"{type(e).__name__}: {e}"


def _create_inferencer(args):
    try:
        from mmpose.apis import MMPoseInferencer  # type: ignore
    except Exception as e:
        raise ModuleNotFoundError(
            "자동 MMPose 실행을 위해 mmpose가 필요합니다. "
            "mmpose 설치 후 다시 실행하거나, 이미 생성된 CameraN.json을 --mmpose_dir에 제공하세요."
        ) from e

    kwargs = {}
    if args.mmpose_pose2d is not None:
        kwargs["pose2d"] = args.mmpose_pose2d
    if args.mmpose_pose2d_weights:
        kwargs["pose2d_weights"] = args.mmpose_pose2d_weights
    if args.mmpose_det_model:
        kwargs["det_model"] = args.mmpose_det_model
    if args.mmpose_det_weights:
        kwargs["det_weights"] = args.mmpose_det_weights
    if args.mmpose_device:
        kwargs["device"] = args.mmpose_device

    try:
        return MMPoseInferencer(**kwargs)
    except TypeError:
        # older versions may not accept all kwargs; retry with a minimal set
        fallback = {}
        if args.mmpose_pose2d is not None:
            fallback["pose2d"] = args.mmpose_pose2d
        if args.mmpose_device:
            fallback["device"] = args.mmpose_device
        return MMPoseInferencer(**fallback)


def auto_generate_mmpose_jsons(
    *,
    undist_root: Path,
    mmpose_dir: Path,
    num_cameras: int,
    data_module,
    multiview_utils_module,
    detector_type: int,
    confidence: float,
    args,
) -> dict:
    mmpose_dir.mkdir(parents=True, exist_ok=True)
    inferencer = _create_inferencer(args)

    summary = {"mmpose_dir": str(mmpose_dir), "cameras": [], "warnings": []}
    vis_root = _get_mmpose_vis_root(args, mmpose_dir)
    if vis_root is not None:
        vis_root.mkdir(parents=True, exist_ok=True)
        summary["mmpose_vis_dir"] = str(vis_root)

    for cam_idx in range(1, num_cameras + 1):
        out_json = mmpose_dir / f"Camera{cam_idx}.json"
        cam_dir = undist_root / f"Camera{cam_idx}"
        if not cam_dir.is_dir():
            raise FileNotFoundError(f"Missing undistorted camera folder for auto MMPose: {cam_dir}")

        image_paths = _iter_image_paths(cam_dir)
        if not image_paths:
            raise RuntimeError(f"No images found for auto MMPose in: {cam_dir}")

        normalized_frames = []
        raw_frame_count = 0
        raw_instance_count = 0
        saved_vis_count = 0
        vis_cam_dir = (vis_root / f"Camera{cam_idx}") if vis_root is not None else None
        vis_kpt_thr = float(getattr(args, "mmpose_vis_kpt_thr", getattr(args, "mmpose_kpt_thr", 0.0)))

        for seq_idx, img_path in enumerate(image_paths):
            try:
                result = next(
                    inferencer(
                        str(img_path),
                        return_vis=False,
                        show=False,
                        bbox_thr=float(args.mmpose_bbox_thr),
                        kpt_thr=float(args.mmpose_kpt_thr),
                    )
                )
            except TypeError:
                result = next(inferencer(str(img_path)))
            except StopIteration:
                result = {}

            preds = result.get("predictions", [])
            if isinstance(preds, list) and len(preds) == 1 and isinstance(preds[0], list):
                instances = preds[0]
            elif isinstance(preds, list):
                instances = preds
            else:
                instances = []

            raw_frame_count += 1
            frame_instances = []
            for inst in instances:
                norm = _normalize_instance(inst, seq_idx, img_path, float(args.mmpose_bbox_thr))
                if norm is None:
                    continue
                frame_instances.append(norm)
                raw_instance_count += 1

            normalized_frames.append(
                {
                    "frame_id": int(seq_idx),
                    "img_id": int(seq_idx),
                    "image_id": int(seq_idx),
                    "image_path": str(img_path),
                    "instances": frame_instances,
                }
            )

            if vis_cam_dir is not None and _should_save_mmpose_vis(seq_idx, saved_vis_count, args):
                out_vis = vis_cam_dir / f"{seq_idx:08d}_{img_path.stem}_pose.jpg"
                if _save_mmpose_visualization(img_path, frame_instances, out_vis, vis_kpt_thr):
                    saved_vis_count += 1

        candidates = _build_candidates(normalized_frames)
        selected_name = None
        selected_obj = None
        validation_log = []

        for name, cand in candidates:
            ok, n_valid, err = _validate_candidate_with_cascalib(
                data_module, multiview_utils_module, cand, detector_type, confidence
            )
            validation_log.append(
                {"candidate": name, "ok": bool(ok), "valid_frame_count": int(n_valid), "error": err}
            )
            if ok:
                selected_name = name
                selected_obj = cand
                break

        if selected_obj is None:
            raise RuntimeError(
                f"Camera{cam_idx}: 자동 MMPose 결과를 CasCalib dataloader 형식으로 맞추지 못했습니다.\n"
                f"Validation tried: {json.dumps(validation_log, ensure_ascii=False, indent=2)}"
            )

        save_json(out_json, selected_obj)
        summary["cameras"].append(
            {
                "camera": f"Camera{cam_idx}",
                "undistorted_dir": str(cam_dir),
                "output_json": str(out_json),
                "raw_frame_count": raw_frame_count,
                "raw_instance_count": raw_instance_count,
                "selected_json_schema": selected_name,
                "mmpose_visualization_dir": str(vis_cam_dir) if vis_cam_dir is not None else None,
                "saved_visualization_count": saved_vis_count,
                "validation": validation_log,
            }
        )

    save_json(mmpose_dir / "_auto_mmpose_summary.json", summary)
    return summary



# -----------------------------------------------------------------------------
# Bundle-adjustment pose dictionary helpers
# -----------------------------------------------------------------------------

COCO17_NAMES_FOR_BA = [
    "nose",
    "left_eye", "right_eye",
    "left_ear", "right_ear",
    "left_shoulder", "right_shoulder",
    "left_elbow", "right_elbow",
    "left_wrist", "right_wrist",
    "left_hip", "right_hip",
    "left_knee", "right_knee",
    "left_ankle", "right_ankle",
]


def _score_of_keypoint_for_ba(person: Mapping, name: str, default: float = 0.0) -> float:
    val = person.get(name)
    if val is None:
        return float(default)
    arr = np.asarray(val, dtype=np.float64).reshape(-1)
    if arr.size >= 3:
        return float(arr[2])
    return float(default)


def _xyc_of_keypoint_for_ba(person: Mapping, name: str) -> Optional[List[float]]:
    val = person.get(name)
    if val is None:
        return None
    arr = np.asarray(val, dtype=np.float64).reshape(-1)
    if arr.size < 2:
        return None
    conf = float(arr[2]) if arr.size >= 3 else 1.0
    return [float(arr[0]), float(arr[1]), conf]


def _mean_xy_for_ba(points: Sequence[Optional[List[float]]]) -> Optional[List[float]]:
    valid = []
    for p in points:
        if p is None:
            continue
        arr = np.asarray(p, dtype=np.float64).reshape(-1)
        if arr.size >= 2 and np.isfinite(arr[0]) and np.isfinite(arr[1]):
            valid.append(arr[:2])
    if not valid:
        return None
    xy = np.mean(np.stack(valid, axis=0), axis=0)
    return [float(xy[0]), float(xy[1])]


def build_pose_dictionary_for_bundle_adjustment(datastore, cond_tol: float = 0.8) -> OrderedDict:
    """Build the nested pose structure expected by CasCalib BA.

    CasCalib's bundle_adjustment.match_3d_plotly_input2d_farthest_point() expects::

        all_points_array[cam][frame][person_id] = [x_anchor, y_anchor, head_x, head_y, full_pose_dict]

    The matching code uses the first two values as the 2D point to intersect
    with the ground plane, and uses element 4 as the full COCO-17 pose dict.
    The full pose dict must have 17 keypoints followed by an ``id`` field because
    the BA code later does ``list(full_pose_dict.values())[:-1]``.
    """
    init_dict = datastore.getData()
    out: OrderedDict = OrderedDict()

    for fr in sorted(init_dict.keys()):
        frame_people = init_dict[fr]
        frame_out: OrderedDict = OrderedDict()
        used_ids = set()

        for local_idx, person in enumerate(frame_people):
            if not isinstance(person, Mapping):
                continue

            left_ankle = _xyc_of_keypoint_for_ba(person, "left_ankle")
            right_ankle = _xyc_of_keypoint_for_ba(person, "right_ankle")
            left_shoulder = _xyc_of_keypoint_for_ba(person, "left_shoulder")
            right_shoulder = _xyc_of_keypoint_for_ba(person, "right_shoulder")
            nose = _xyc_of_keypoint_for_ba(person, "nose")

            avg_cond = (
                _score_of_keypoint_for_ba(person, "left_ankle")
                + _score_of_keypoint_for_ba(person, "right_ankle")
                + _score_of_keypoint_for_ba(person, "left_shoulder")
                + _score_of_keypoint_for_ba(person, "right_shoulder")
            ) / 4.0
            if avg_cond < cond_tol:
                continue

            ankle_xy = _mean_xy_for_ba([left_ankle, right_ankle])
            if ankle_xy is None:
                # Last-resort fallback. This should be rare, but avoids crashing
                # if one dataset has weak ankle scores after MMPose conversion.
                ankle_xy = _mean_xy_for_ba([left_shoulder, right_shoulder, nose])
            head_xy = _mean_xy_for_ba([nose]) or _mean_xy_for_ba([left_shoulder, right_shoulder])
            if ankle_xy is None or head_xy is None:
                continue

            try:
                person_id = int(person.get("id", local_idx))
            except Exception:
                person_id = int(local_idx)
            while person_id in used_ids:
                person_id += 1
            used_ids.add(person_id)

            pose_for_ba: OrderedDict = OrderedDict()
            missing = False
            for name in COCO17_NAMES_FOR_BA:
                xyc = _xyc_of_keypoint_for_ba(person, name)
                if xyc is None:
                    missing = True
                    break
                pose_for_ba[name] = xyc
            if missing or len(pose_for_ba) != 17:
                continue

            # Keep this last. CasCalib BA uses list(values())[:-1], so this id is dropped.
            pose_for_ba["id"] = int(person_id)

            # IMPORTANT: CasCalib's matcher expects a 5-element container here,
            # not the pose dict itself. Element 4 is the full pose dict.
            frame_out[int(person_id)] = [
                float(ankle_xy[0]),
                float(ankle_xy[1]),
                float(head_xy[0]),
                float(head_xy[1]),
                pose_for_ba,
            ]

        if len(frame_out) > 0:
            out[int(fr)] = frame_out

    return out

# -----------------------------------------------------------------------------
# Raw smartphone image preparation + final merge helpers
# -----------------------------------------------------------------------------

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff", ".webp"}


# -----------------------------------------------------------------------------
# Basic JSON / path helpers
# -----------------------------------------------------------------------------

def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, ensure_ascii=False), encoding="utf-8")


def find_existing_cascalib_repo(root: Path, user_path: Optional[str]) -> Path:
    if user_path:
        p = Path(user_path).expanduser()
        if not p.is_absolute():
            p = root / p
        if p.is_dir():
            return p
        raise NotADirectoryError(f"CasCalib repo not found: {p}")

    candidates = [
        root / "CasCalib",
        root / "cascalib",
        root / "CASCALIB",
    ]
    for p in candidates:
        if (p / "configuration.json").exists() and (p / "run_calibration_ransac.py").exists():
            return p

    raise FileNotFoundError(
        "CasCalib repo를 찾지 못했습니다. 기본 위치는 /home/curica/capstone_real/CasCalib 입니다. "
        "--cascalib_repo 로 직접 지정하세요."
    )


def list_images(folder: Path) -> List[Path]:
    if not folder.is_dir():
        raise NotADirectoryError(f"Image folder not found: {folder}")
    imgs = [p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in IMAGE_EXTS]
    if not imgs:
        raise RuntimeError(f"No images found in: {folder}")
    return imgs


def sidecar_metadata_path(img_path: Path) -> Optional[Path]:
    candidates = [
        img_path.with_name(img_path.name + ".metadata.json"),          # frame.jpg.metadata.json
        img_path.with_suffix(img_path.suffix + ".metadata.json"),      # frame.jpg.metadata.json
        img_path.with_suffix(".metadata.json"),                        # frame.metadata.json
        img_path.with_name(img_path.stem + ".json"),                   # frame.json
    ]
    for p in candidates:
        if p.exists():
            return p
    return None


def parse_timestamp_from_filename_seconds(name: str) -> Optional[float]:
    # Smartphone files often start with a 13-digit millisecond timestamp.
    m = re.search(r"(\d{13})", name)
    if m:
        return float(int(m.group(1)) / 1000.0)

    # Fallback for nanosecond-like 16~19 digit timestamps.
    m = re.search(r"(\d{16,19})", name)
    if m:
        val = int(m.group(1))
        # Treat as ns if very large.
        if val > 10**15:
            return float(val / 1e9)
    return None


def read_frame_record(img_path: Path, fallback_idx: int, fps_fallback: float) -> Dict[str, Any]:
    meta_path = sidecar_metadata_path(img_path)
    meta = {}
    if meta_path:
        try:
            raw = read_json(meta_path)
            meta = raw.get("metadata", raw) if isinstance(raw, dict) else {}
        except Exception as e:
            print(f"[warn] Failed to read metadata {meta_path}: {e}", file=sys.stderr)

    ts = None
    ts_source = None

    if isinstance(meta, dict) and meta.get("device_timestamp_ms") not in (None, ""):
        try:
            ts = float(meta["device_timestamp_ms"]) / 1000.0
            ts_source = "metadata.device_timestamp_ms"
        except Exception:
            pass

    if ts is None and isinstance(meta, dict) and meta.get("timestamp_sec") not in (None, ""):
        try:
            ts = float(meta["timestamp_sec"])
            ts_source = "metadata.timestamp_sec"
        except Exception:
            pass

    if ts is None:
        ts = parse_timestamp_from_filename_seconds(img_path.name)
        if ts is not None:
            ts_source = "filename_timestamp"

    if ts is None:
        ts = float(fallback_idx / max(fps_fallback, 1e-9))
        ts_source = f"sequential_index_assuming_{fps_fallback:g}fps"

    frame_sequence = None
    if isinstance(meta, dict) and meta.get("frame_sequence") not in (None, ""):
        try:
            frame_sequence = int(meta["frame_sequence"])
        except Exception:
            frame_sequence = None

    return {
        "path": img_path,
        "metadata_path": str(meta_path) if meta_path else None,
        "metadata": meta if isinstance(meta, dict) else {},
        "timestamp_sec": float(ts),
        "timestamp_source": ts_source,
        "frame_sequence": frame_sequence,
    }


# -----------------------------------------------------------------------------
# Intrinsic JSON parser
# -----------------------------------------------------------------------------

def canonical_camera_keys(cam_idx: int) -> List[str]:
    return [
        f"Camera{cam_idx}",
        f"camera{cam_idx}",
        f"CAMERA{cam_idx}",
        f"camera_{cam_idx:02d}",
        f"camera_{cam_idx}",
        f"cam{cam_idx}",
        f"cam_{cam_idx:02d}",
        f"android_device_{cam_idx:03d}",
        str(cam_idx),
        f"Camera{cam_idx}_IOP",
        f"camera{cam_idx}_IOP",
    ]


INTRINSIC_MATRIX_KEYS = [
    "camera_matrix",
    "K",
    "intrinsic_matrix",
    "intrinsics_matrix",
    "mtx",
]

DISTORTION_KEYS = [
    "dist_coeffs",
    "distortion_coefficients",
    "distortion",
    "distCoeffs",
    "D",
]

NESTED_INTRINSIC_KEYS = [
    "intrinsics",
    "intrinsic",
    "calibration",
    "camera_calibration",
    "camera_parameters",
    "parameters",
    "result",
]

CAMERA_ID_FIELD_KEYS = [
    "camera",
    "camera_name",
    "camera_id",
    "name",
    "id",
    "device_id",
    "folder",
    "path",
    "source_dir",
]


def numeric_id_from_text(text: str) -> Optional[int]:
    """Extract a camera number from common strings.

    Examples that map to 1:
    Camera1, camera_01, android_device_001, camera1_10fps,
    SingleCamera1_IOP, 001.
    """
    lower = str(text).lower()
    patterns = [
        r"camera[_\- ]?0*([1-9]\d*)",
        r"cam[_\- ]?0*([1-9]\d*)",
        r"android[_\- ]?device[_\- ]?0*([1-9]\d*)",
        r"device[_\- ]?0*([1-9]\d*)",
        r"singlecamera[_\- ]?0*([1-9]\d*)",
    ]
    for pat in patterns:
        m = re.search(pat, lower)
        if m:
            return int(m.group(1))
    # Use a bare number only when the whole text is a number. This avoids
    # accidentally treating focal lengths or image sizes as camera IDs.
    if re.fullmatch(r"0*[1-9]\d*", lower):
        return int(lower)
    return None


def first_present(d: Dict[str, Any], keys: List[str]) -> Any:
    for key in keys:
        if key in d:
            return d[key]
    return None


def has_intrinsic_matrix_key(d: Mapping[str, Any]) -> bool:
    return any(k in d for k in INTRINSIC_MATRIX_KEYS)


def has_distortion_key(d: Mapping[str, Any]) -> bool:
    return any(k in d for k in DISTORTION_KEYS)


def shallow_camera_id_fields(d: Mapping[str, Any]) -> Dict[str, Any]:
    return {k: d[k] for k in CAMERA_ID_FIELD_KEYS if k in d}


def make_merged_intrinsic_block(parent: Mapping[str, Any], child: Mapping[str, Any]) -> Dict[str, Any]:
    """Merge parent camera ID/size metadata with a nested intrinsic block."""
    merged: Dict[str, Any] = {}
    for key in CAMERA_ID_FIELD_KEYS + ["image_size", "resolution", "size", "width", "height", "image_width", "image_height"]:
        if key in parent:
            merged[key] = parent[key]
    merged.update(dict(child))
    return merged


def iter_intrinsic_candidates(obj: Any, path: str = "root") -> Iterable[Tuple[str, Dict[str, Any]]]:
    """Yield every dictionary that looks like an intrinsic block.

    This intentionally supports many JSON layouts, for example:
      {"Camera1": {"camera_matrix": ...}}
      {"results": [{"camera_id": "camera_01", "camera_matrix": ...}]}
      {"cameras": [{"camera_id": "camera_01", "intrinsics": {"K": ...}}]}
      [{"camera_name": "Camera1", "camera_matrix": ...}, ...]
    """
    if isinstance(obj, dict):
        if has_intrinsic_matrix_key(obj):
            yield path, dict(obj)

        # Common pattern: the item has camera_id, but K/dist are under item["intrinsics"].
        for child_key in NESTED_INTRINSIC_KEYS:
            child = obj.get(child_key)
            if isinstance(child, dict) and has_intrinsic_matrix_key(child):
                yield f"{path}.{child_key}", make_merged_intrinsic_block(obj, child)

        for key, val in obj.items():
            if isinstance(val, (dict, list)):
                yield from iter_intrinsic_candidates(val, f"{path}.{key}")

    elif isinstance(obj, list):
        for i, item in enumerate(obj):
            if isinstance(item, (dict, list)):
                yield from iter_intrinsic_candidates(item, f"{path}[{i}]")


def camera_match_score(block: Mapping[str, Any], path: str, cam_idx: int) -> int:
    """Score how likely an intrinsic block belongs to Camera{cam_idx}."""
    score = 0

    # Strongest evidence: explicit camera/name/id fields.
    for key in CAMERA_ID_FIELD_KEYS:
        val = block.get(key)
        if val is None:
            continue
        n = numeric_id_from_text(str(val))
        if n == cam_idx:
            score += 100
        elif n is not None and n != cam_idx:
            score -= 100

    # Next: JSON path such as root.Camera1 or root.results[0].Camera1_IOP.
    n_path = numeric_id_from_text(path)
    if n_path == cam_idx:
        score += 60
    elif n_path is not None and n_path != cam_idx:
        score -= 60

    # Weak evidence: exact canonical key appears in the path text.
    lower_path = path.lower()
    for key in canonical_camera_keys(cam_idx):
        if str(key).lower() in lower_path:
            score += 20
            break

    return score


def select_nth_candidate_if_unlabeled(candidates: List[Tuple[str, Dict[str, Any]]], cam_idx: int) -> Optional[Dict[str, Any]]:
    """Fallback for a pure list layout with no camera_id fields.

    If every intrinsic block is unlabeled but the list order is Camera1, Camera2, ...,
    use the N-th block. This is less ideal than explicit camera IDs, but it is a
    common export format from simple calibration scripts.
    """
    if len(candidates) < cam_idx:
        return None

    # Avoid this fallback if any candidate contains a camera-like ID. In that case
    # a failed match likely means the JSON IDs are inconsistent and should not be
    # silently interpreted by order.
    for path, block in candidates:
        texts = [path] + [str(block.get(k)) for k in CAMERA_ID_FIELD_KEYS if block.get(k) is not None]
        if any(numeric_id_from_text(t) is not None for t in texts):
            return None
    return candidates[cam_idx - 1][1]


def choose_camera_block(intr_all: Any, cam_idx: int) -> Dict[str, Any]:
    # Case 1: top-level has per-camera dictionaries.
    # Example: {"Camera1": {...}, "Camera2": {...}}
    if isinstance(intr_all, dict):
        for key in canonical_camera_keys(cam_idx):
            if key in intr_all and isinstance(intr_all[key], dict):
                val = intr_all[key]
                if has_intrinsic_matrix_key(val):
                    return dict(val)
                for child_key in NESTED_INTRINSIC_KEYS:
                    child = val.get(child_key)
                    if isinstance(child, dict) and has_intrinsic_matrix_key(child):
                        return make_merged_intrinsic_block(val, child)

        # Case 1b: common export from this project:
        # {"board": {...}, "cameras": {"Camera1": {...}, "Camera2": {...}}}
        cameras_obj = intr_all.get("cameras")
        if isinstance(cameras_obj, dict):
            for key in canonical_camera_keys(cam_idx):
                if key in cameras_obj and isinstance(cameras_obj[key], dict):
                    val = cameras_obj[key]
                    if has_intrinsic_matrix_key(val):
                        return dict(val)
                    for child_key in NESTED_INTRINSIC_KEYS:
                        child = val.get(child_key)
                        if isinstance(child, dict) and has_intrinsic_matrix_key(child):
                            return make_merged_intrinsic_block(val, child)

            # Also support camera keys with small spelling changes by scoring all nested candidates.
            nested_candidates = list(iter_intrinsic_candidates(cameras_obj, "root.cameras"))
            nested_scored = [(camera_match_score(block, path, cam_idx), path, block) for path, block in nested_candidates]
            nested_scored = [x for x in nested_scored if x[0] > 0]
            if nested_scored:
                nested_scored.sort(key=lambda x: x[0], reverse=True)
                return nested_scored[0][2]

        # Case 2: top-level has a list of camera blocks under a common key.
        for list_key in (
            "cameras",
            "camera",
            "intrinsics",
            "all_cameras",
            "results",
            "camera_results",
            "calibrations",
            "camera_intrinsics",
            "all_intrinsics",
        ):
            val = intr_all.get(list_key)
            if isinstance(val, list):
                candidates = list(iter_intrinsic_candidates(val, f"root.{list_key}"))
                scored = [(camera_match_score(block, path, cam_idx), path, block) for path, block in candidates]
                scored = [x for x in scored if x[0] > 0]
                if scored:
                    scored.sort(key=lambda x: x[0], reverse=True)
                    return scored[0][2]
                nth = select_nth_candidate_if_unlabeled(candidates, cam_idx)
                if nth is not None:
                    return nth

        # Case 3: arbitrary top-level keys whose text contains camera index.
        for key, val in intr_all.items():
            if isinstance(val, dict) and numeric_id_from_text(str(key)) == cam_idx:
                if has_intrinsic_matrix_key(val):
                    return dict(val)
                for child_key in NESTED_INTRINSIC_KEYS:
                    child = val.get(child_key)
                    if isinstance(child, dict) and has_intrinsic_matrix_key(child):
                        return make_merged_intrinsic_block(val, child)

    # Case 4: fully recursive search for any intrinsic-looking block.
    candidates = list(iter_intrinsic_candidates(intr_all))
    scored = [(camera_match_score(block, path, cam_idx), path, block) for path, block in candidates]
    scored_pos = [x for x in scored if x[0] > 0]
    if scored_pos:
        scored_pos.sort(key=lambda x: x[0], reverse=True)
        return scored_pos[0][2]

    # Case 5: top-level list or unlabeled recursive list: assume order Camera1,2,3.
    nth = select_nth_candidate_if_unlabeled(candidates, cam_idx)
    if nth is not None:
        return nth

    # Better diagnostics than the previous error.
    if isinstance(intr_all, dict):
        top_keys = list(intr_all.keys())[:30]
    elif isinstance(intr_all, list):
        top_keys = [f"top-level list length={len(intr_all)}"]
    else:
        top_keys = [f"top-level type={type(intr_all).__name__}"]
    candidate_paths = [path for path, _block in candidates[:30]]

    raise KeyError(
        f"all_cameras_intrinsics.json에서 Camera{cam_idx} 내부표정요소를 찾지 못했습니다.\n"
        f"지원 예: Camera{cam_idx}, camera{cam_idx}, camera_{cam_idx:02d}, "
        f"cameras[].camera_id, results[].camera_name, 또는 순서형 리스트.\n"
        f"현재 top-level 키/형태: {top_keys}\n"
        f"발견된 intrinsic 후보 경로: {candidate_paths}"
    )


def find_nested_dict_with_key(block: Dict[str, Any], keys: List[str]) -> Optional[Dict[str, Any]]:
    for child_key in NESTED_INTRINSIC_KEYS:
        child = block.get(child_key)
        if isinstance(child, dict) and any(k in child for k in keys):
            return child
    return None


def parse_intrinsic_block(block: Dict[str, Any], cam_idx: int) -> Dict[str, Any]:
    matrix_source = block
    K_raw = first_present(matrix_source, INTRINSIC_MATRIX_KEYS)
    if K_raw is None:
        nested = find_nested_dict_with_key(block, INTRINSIC_MATRIX_KEYS)
        if nested is not None:
            matrix_source = nested
            K_raw = first_present(matrix_source, INTRINSIC_MATRIX_KEYS)
    if K_raw is None:
        raise KeyError(f"Camera{cam_idx}: camera_matrix/K 필드가 없습니다. block keys={list(block.keys())}")

    K = np.asarray(K_raw, dtype=np.float64)
    if K.shape == (9,):
        K = K.reshape(3, 3)
    if K.shape != (3, 3):
        raise ValueError(f"Camera{cam_idx}: camera matrix shape must be 3x3, got {K.shape}")

    dist_raw = first_present(matrix_source, DISTORTION_KEYS)
    if dist_raw is None:
        nested_dist = find_nested_dict_with_key(block, DISTORTION_KEYS)
        if nested_dist is not None:
            dist_raw = first_present(nested_dist, DISTORTION_KEYS)
    if dist_raw is None:
        dist = np.zeros((5,), dtype=np.float64)
    else:
        dist = np.asarray(dist_raw, dtype=np.float64).reshape(-1)

    # Image size can be either in the camera block or inside the nested intrinsic block.
    image_size = first_present(block, ["image_size", "resolution", "size"])
    if image_size is None and matrix_source is not block:
        image_size = first_present(matrix_source, ["image_size", "resolution", "size"])

    width = first_present(block, ["width", "image_width", "w"])
    height = first_present(block, ["height", "image_height", "h"])
    if width is None and matrix_source is not block:
        width = first_present(matrix_source, ["width", "image_width", "w"])
    if height is None and matrix_source is not block:
        height = first_present(matrix_source, ["height", "image_height", "h"])

    if isinstance(image_size, dict):
        width = image_size.get("width", image_size.get("w", width))
        height = image_size.get("height", image_size.get("h", height))
    elif isinstance(image_size, (list, tuple)) and len(image_size) >= 2:
        width = image_size[0]
        height = image_size[1]

    return {
        "camera_matrix": K,
        "dist_coeffs": dist,
        "image_size_from_intrinsics": {
            "width": int(width) if width is not None else None,
            "height": int(height) if height is not None else None,
        },
        "raw_block": block,
    }


def load_all_intrinsics(path: Path, num_cameras: int) -> Dict[int, Dict[str, Any]]:
    intr_all = read_json(path)
    out = {}
    for cam_idx in range(1, num_cameras + 1):
        block = choose_camera_block(intr_all, cam_idx)
        out[cam_idx] = parse_intrinsic_block(block, cam_idx)
    return out


# -----------------------------------------------------------------------------
# Intrinsics rotation helpers
# -----------------------------------------------------------------------------

def metadata_rotation_degrees(records: Sequence[Dict[str, Any]]) -> Optional[int]:
    """Return the first rotation_fix_degrees_ccw_positive found in sidecar metadata.

    The rotated_10fps dataset stores images after rotation correction.  The
    ChArUco intrinsics may still be in the original portrait coordinate system
    (1080x1920).  This value tells us whether each camera image was rotated left
    (+90) or right (-90) to become the current landscape image (1920x1080).
    """
    for rec in records:
        meta = rec.get("metadata", {})
        if not isinstance(meta, dict):
            continue
        val = meta.get("rotation_fix_degrees_ccw_positive")
        if val in (None, ""):
            continue
        try:
            return int(float(val))
        except Exception:
            continue
    return None


def rotate_intrinsic_matrix_90(K: np.ndarray, original_width: int, original_height: int, degrees_ccw: int) -> np.ndarray:
    """Rotate a no-skew pinhole K for an already-rotated image.

    original_width/original_height are the image dimensions that the input K was
    calibrated on.  The returned K is for the image after the requested rotation.
    Distortion coefficients are kept unchanged by the caller.
    """
    K = np.asarray(K, dtype=np.float64)
    fx = float(K[0, 0])
    fy = float(K[1, 1])
    cx = float(K[0, 2])
    cy = float(K[1, 2])
    deg = int(degrees_ccw) % 360

    out = np.eye(3, dtype=np.float64)
    if deg == 90:      # left / CCW: x' = y, y' = W - 1 - x
        out[0, 0] = fy
        out[1, 1] = fx
        out[0, 2] = cy
        out[1, 2] = float(original_width - 1) - cx
    elif deg == 270:   # right / CW: x' = H - 1 - y, y' = x
        out[0, 0] = fy
        out[1, 1] = fx
        out[0, 2] = float(original_height - 1) - cy
        out[1, 2] = cx
    elif deg == 180:
        out[0, 0] = fx
        out[1, 1] = fy
        out[0, 2] = float(original_width - 1) - cx
        out[1, 2] = float(original_height - 1) - cy
    elif deg == 0:
        out = K.copy()
    else:
        raise ValueError(f"Only 0/90/180/270 degree K rotation is supported, got {degrees_ccw}")
    return out


def maybe_rotate_intrinsics_to_image(
    *,
    cam_idx: int,
    K: np.ndarray,
    intrinsic_size: Mapping[str, Any],
    image_width: int,
    image_height: int,
    records: Sequence[Dict[str, Any]],
) -> Tuple[np.ndarray, Optional[str], Dict[str, Any]]:
    """Convert K when intrinsic JSON is portrait but images are already rotated.

    Returns: (K_for_current_images, warning_or_info, updated_intrinsic_size)
    """
    exp_w = intrinsic_size.get("width") if isinstance(intrinsic_size, Mapping) else None
    exp_h = intrinsic_size.get("height") if isinstance(intrinsic_size, Mapping) else None
    if exp_w is None or exp_h is None:
        return np.asarray(K, dtype=np.float64), None, dict(intrinsic_size or {})

    exp_w_i = int(exp_w)
    exp_h_i = int(exp_h)
    if exp_w_i == int(image_width) and exp_h_i == int(image_height):
        return np.asarray(K, dtype=np.float64), None, {"width": exp_w_i, "height": exp_h_i}

    # Common case here: intrinsics are 1080x1920, rotated frames are 1920x1080.
    if exp_w_i == int(image_height) and exp_h_i == int(image_width):
        deg = metadata_rotation_degrees(records)
        if deg is None:
            msg = (
                f"Camera{cam_idx}: image size is {image_width}x{image_height}, "
                f"but intrinsic JSON says {exp_w_i}x{exp_h_i}. Sizes are swapped, "
                "but sidecar metadata has no rotation_fix_degrees_ccw_positive, so K was not rotated."
            )
            return np.asarray(K, dtype=np.float64), msg, {"width": exp_w_i, "height": exp_h_i}
        try:
            K_rot = rotate_intrinsic_matrix_90(np.asarray(K, dtype=np.float64), exp_w_i, exp_h_i, deg)
        except Exception as e:
            msg = (
                f"Camera{cam_idx}: failed to rotate intrinsic K from {exp_w_i}x{exp_h_i} "
                f"to {image_width}x{image_height}: {e}"
            )
            return np.asarray(K, dtype=np.float64), msg, {"width": exp_w_i, "height": exp_h_i}
        msg = (
            f"Camera{cam_idx}: intrinsic K was auto-rotated by {deg} deg CCW "
            f"because intrinsic size {exp_w_i}x{exp_h_i} is swapped relative to image size "
            f"{image_width}x{image_height}."
        )
        return K_rot, msg, {"width": int(image_width), "height": int(image_height), "rotated_from": {"width": exp_w_i, "height": exp_h_i}, "rotation_degrees_ccw": int(deg)}

    msg = (
        f"Camera{cam_idx}: image size is {image_width}x{image_height}, "
        f"but intrinsic JSON says {exp_w_i}x{exp_h_i}. K가 현재 이미지 기준인지 확인하세요."
    )
    return np.asarray(K, dtype=np.float64), msg, {"width": exp_w_i, "height": exp_h_i}

# -----------------------------------------------------------------------------
# Image preparation
# -----------------------------------------------------------------------------

def import_cv2():
    try:
        import cv2  # type: ignore
        return cv2
    except Exception as e:
        raise RuntimeError(
            "OpenCV(cv2)가 필요합니다. 예: pip install opencv-contrib-python-headless==4.8.1.78"
        ) from e


def estimate_fps(records: List[Dict[str, Any]], fallback: float) -> float:
    ts = np.array([r["timestamp_sec"] for r in records], dtype=np.float64)
    if len(ts) < 2:
        return float(fallback)
    diffs = np.diff(np.sort(ts))
    diffs = diffs[diffs > 1e-6]
    if len(diffs) == 0:
        return float(fallback)
    return float(1.0 / np.median(diffs))


def make_synthetic_intrinsic(width: int, height: int, focal_scale: float = 1.0) -> Dict[str, Any]:
    """Create a simple initial K when ChArUco intrinsics are intentionally not used.

    This K is only a bootstrap value required by this wrapper's metadata layout.
    With --single_view_k_policy estimated, CasCalib replaces it downstream with
    the single-view focal estimated from human pose.
    """
    f = float(max(width, height)) * float(focal_scale)
    return {
        "camera_matrix": [
            [f, 0.0, (float(width) - 1.0) / 2.0],
            [0.0, f, (float(height) - 1.0) / 2.0],
            [0.0, 0.0, 1.0],
        ],
        "dist_coeffs": [0.0, 0.0, 0.0, 0.0, 0.0],
        "image_size_from_intrinsics": {"width": int(width), "height": int(height)},
        "source": "synthetic_no_charuco",
    }


def prepare_one_camera(
    *,
    cam_idx: int,
    src_dir: Path,
    out_img_dir: Path,
    meta_dir: Path,
    intrinsic: Optional[Dict[str, Any]],
    no_undistort: bool,
    no_charuco: bool,
    synthetic_focal_scale: float,
    undistort_alpha: float,
    force: bool,
    fps_fallback: float,
) -> Dict[str, Any]:
    cv2 = import_cv2()

    out_img_dir.mkdir(parents=True, exist_ok=True)
    meta_dir.mkdir(parents=True, exist_ok=True)

    images = list_images(src_dir)
    records = [read_frame_record(p, i, fps_fallback) for i, p in enumerate(images)]
    records.sort(key=lambda r: (r["timestamp_sec"], r["frame_sequence"] if r["frame_sequence"] is not None else 10**12, r["path"].name))

    frame_map_csv = meta_dir / f"Camera{cam_idx}_frame_map.csv"
    meta_json = meta_dir / f"Camera{cam_idx}_meta.json"

    if force:
        for old in out_img_dir.glob("*"):
            if old.is_file():
                old.unlink()

    width = None
    height = None
    first_shape_warning = None
    rows = []

    # Read the first frame to know the actual image size that CasCalib will see.
    first_img = cv2.imread(str(records[0]["path"]), cv2.IMREAD_COLOR)
    if first_img is None:
        raise RuntimeError(f"Cannot read first image: {records[0]['path']}")
    height, width = first_img.shape[:2]

    if no_charuco or intrinsic is None:
        # No ChArUco mode: do not undistort and do not rotate/convert any K.
        # The images are copied exactly as they are.  A synthetic K is stored only
        # so the rest of the wrapper has a valid metadata field.  Downstream,
        # --single_view_k_policy estimated should be used so CasCalib uses its
        # own pose-based focal estimate.
        intrinsic = make_synthetic_intrinsic(int(width), int(height), synthetic_focal_scale)
        K_from_json = np.asarray(intrinsic["camera_matrix"], dtype=np.float64)
        dist_original = np.asarray(intrinsic["dist_coeffs"], dtype=np.float64).reshape(-1)
        K_original = K_from_json.copy()
        effective_intrinsic_size = {
            "width": int(width),
            "height": int(height),
            "source": "synthetic_no_charuco",
        }
        used_K = K_original.copy()
        used_dist = dist_original.copy()
        undistortion_applied = False
        no_undistort = True
    else:
        K_from_json = np.asarray(intrinsic["camera_matrix"], dtype=np.float64)
        dist_original = np.asarray(intrinsic["dist_coeffs"], dtype=np.float64).reshape(-1)
        dist_is_zero = bool(np.all(np.abs(dist_original) < 1e-12))

        expected = intrinsic.get("image_size_from_intrinsics", {})
        K_original, first_shape_warning, effective_intrinsic_size = maybe_rotate_intrinsics_to_image(
            cam_idx=cam_idx,
            K=K_from_json,
            intrinsic_size=expected,
            image_width=int(width),
            image_height=int(height),
            records=records,
        )
        if first_shape_warning:
            print(f"[warn] {first_shape_warning}", file=sys.stderr)

        used_K = K_original.copy()
        used_dist = np.zeros_like(dist_original)
        undistortion_applied = False

        if not no_undistort and not dist_is_zero:
            used_K, _roi = cv2.getOptimalNewCameraMatrix(
                K_original,
                dist_original,
                (int(width), int(height)),
                float(undistort_alpha),
                (int(width), int(height)),
            )
            used_dist = np.zeros_like(dist_original)
            undistortion_applied = True
        else:
            used_K = K_original.copy()
            used_dist = dist_original.copy() if no_undistort else np.zeros_like(dist_original)

    with open(frame_map_csv, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow([
            "seq_index",
            "timestamp_sec",
            "out_name",
            "source_name",
            "source_path",
            "metadata_path",
            "timestamp_source",
            "frame_sequence",
            "device_timestamp_ms",
            "sensor_timestamp_ns",
        ])

        for seq_idx, rec in enumerate(records):
            src = rec["path"]
            out_name = f"{seq_idx:08d}.jpg"
            dst = out_img_dir / out_name

            if force or not dst.exists():
                img = cv2.imread(str(src), cv2.IMREAD_COLOR)
                if img is None:
                    raise RuntimeError(f"Cannot read image: {src}")

                if undistortion_applied:
                    img = cv2.undistort(img, K_original, dist_original, None, used_K)

                ok = cv2.imwrite(str(dst), img, [int(cv2.IMWRITE_JPEG_QUALITY), 95])
                if not ok:
                    raise RuntimeError(f"Failed to write image: {dst}")

            meta = rec.get("metadata", {})
            writer.writerow([
                seq_idx,
                f"{rec['timestamp_sec']:.9f}",
                out_name,
                src.name,
                str(src),
                rec.get("metadata_path") or "",
                rec.get("timestamp_source") or "",
                rec.get("frame_sequence") if rec.get("frame_sequence") is not None else "",
                meta.get("device_timestamp_ms", ""),
                meta.get("sensor_timestamp_ns", ""),
            ])

            rows.append({
                "seq_index": seq_idx,
                "timestamp_sec": rec["timestamp_sec"],
                "out_name": out_name,
                "source_name": src.name,
                "source_path": str(src),
                "metadata_path": rec.get("metadata_path"),
                "timestamp_source": rec.get("timestamp_source"),
                "frame_sequence": rec.get("frame_sequence"),
            })

    fps_est = estimate_fps(records, fps_fallback)

    meta_obj = {
        "status": "ok",
        "camera": f"Camera{cam_idx}",
        "camera_id": f"camera_{cam_idx:02d}",
        "source_dir": str(src_dir),
        "undistorted_dir": str(out_img_dir),
        "frame_map_csv": str(frame_map_csv),
        "num_frames": len(records),
        "image_size": {"width": int(width), "height": int(height)},
        "fps_estimate_from_timestamps": fps_est,
        "fps_fallback": float(fps_fallback),
        "timestamp_note": "timestamp_sec uses device_timestamp_ms when available; otherwise filename timestamp; otherwise sequential index/fps_fallback.",
        "preparation_mode": "copy_raw_no_charuco_no_undistort" if no_charuco else "charuco_intrinsics_optional_undistort",
        "camera_matrix": used_K.tolist(),
        "dist_coeffs": used_dist.reshape(-1).tolist(),
        "undistortion_applied": bool(undistortion_applied),
        "undistort_alpha": float(undistort_alpha),
        "original_intrinsics": {
            "camera_matrix_from_json_before_rotation": K_from_json.tolist(),
            "camera_matrix_used_before_undistort": K_original.tolist(),
            "dist_coeffs": dist_original.reshape(-1).tolist(),
            "image_size_from_intrinsics_json": intrinsic.get("image_size_from_intrinsics", {}),
            "effective_image_size_for_intrinsics": effective_intrinsic_size,
        },
        "warnings": [first_shape_warning] if first_shape_warning else [],
    }
    write_json(meta_json, meta_obj)

    return {
        "camera": f"Camera{cam_idx}",
        "source_dir": str(src_dir),
        "prepared_dir": str(out_img_dir),
        "metadata_json": str(meta_json),
        "frame_map_csv": str(frame_map_csv),
        "num_frames": len(records),
        "image_size": {"width": int(width), "height": int(height)},
        "fps_estimate_from_timestamps": fps_est,
        "undistortion_applied": bool(undistortion_applied),
        "camera_matrix_used": used_K.tolist(),
        "dist_coeffs_used": used_dist.reshape(-1).tolist(),
        "original_intrinsics": {
            "camera_matrix_from_json_before_rotation": K_from_json.tolist(),
            "camera_matrix_used_before_undistort": K_original.tolist(),
            "dist_coeffs": dist_original.reshape(-1).tolist(),
            "image_size_from_intrinsics_json": intrinsic.get("image_size_from_intrinsics", {}),
            "effective_image_size_for_intrinsics": effective_intrinsic_size,
        },
        "warnings": [first_shape_warning] if first_shape_warning else [],
    }


def prepare_dataset(args) -> Dict[str, Any]:
    root = Path(args.root).expanduser().resolve()
    input_root = Path(args.input_root).expanduser()
    if not input_root.is_absolute():
        input_root = root / input_root

    intrinsics_json = Path(args.intrinsics_json).expanduser()
    if not intrinsics_json.is_absolute():
        intrinsics_json = root / intrinsics_json

    prepared_dir = Path(args.prepared_dir).expanduser()
    if not prepared_dir.is_absolute():
        prepared_dir = root / prepared_dir

    undist_root = prepared_dir / "undistorted"
    meta_root = prepared_dir / "metadata"
    undist_root.mkdir(parents=True, exist_ok=True)
    meta_root.mkdir(parents=True, exist_ok=True)

    use_charuco = bool(getattr(args, "use_charuco_intrinsics", False))
    if use_charuco:
        if not intrinsics_json.exists():
            raise FileNotFoundError(f"Intrinsics JSON not found: {intrinsics_json}")
        intrinsics = load_all_intrinsics(intrinsics_json, args.num_cameras)
    else:
        intrinsics = {cam_idx: None for cam_idx in range(1, args.num_cameras + 1)}
        print("[prepare] ChArUco intrinsics disabled: raw images will be copied without undistortion.")

    source_dirs = {
        1: input_root / "camera1" / "camera1_10fps",
        2: input_root / "camera2" / "camera2_10fps",
        3: input_root / "camera3" / "camera3_10fps",
    }

    # Optional explicit raw folders, e.g. --camera1_dir /path/to/images.
    for cam_idx in range(1, args.num_cameras + 1):
        explicit = getattr(args, f"camera{cam_idx}_dir", None)
        if explicit:
            p = Path(explicit).expanduser()
            if not p.is_absolute():
                p = root / p
            source_dirs[cam_idx] = p

    summary = {
        "root": str(root),
        "input_root": str(input_root),
        "intrinsics_json": str(intrinsics_json) if use_charuco else None,
        "use_charuco_intrinsics": bool(use_charuco),
        "prepared_dir": str(prepared_dir),
        "undistorted_root": str(undist_root),
        "metadata_root": str(meta_root),
        "num_cameras": int(args.num_cameras),
        "cameras": [],
    }

    for cam_idx in range(1, args.num_cameras + 1):
        src_dir = source_dirs.get(cam_idx, input_root / f"camera{cam_idx}" / f"camera{cam_idx}_10fps")
        result = prepare_one_camera(
            cam_idx=cam_idx,
            src_dir=src_dir,
            out_img_dir=undist_root / f"Camera{cam_idx}",
            meta_dir=meta_root,
            intrinsic=intrinsics[cam_idx],
            no_undistort=True if not use_charuco else args.no_undistort,
            no_charuco=not use_charuco,
            synthetic_focal_scale=args.synthetic_focal_scale,
            undistort_alpha=args.undistort_alpha,
            force=args.force,
            fps_fallback=args.fps_fallback,
        )
        summary["cameras"].append(result)
        print(
            f"[prepare] Camera{cam_idx}: {result['num_frames']} frames -> "
            f"{result['prepared_dir']} | undistort={result['undistortion_applied']}"
        )

    write_json(prepared_dir / "prepare_summary.json", summary)
    print(f"[prepare] summary: {prepared_dir / 'prepare_summary.json'}")
    return summary


# -----------------------------------------------------------------------------
# Run CasCalib and merge final JSON
# -----------------------------------------------------------------------------

def safe_load_json(path: Path) -> Optional[Any]:
    if path.exists():
        return read_json(path)
    return None


def merge_final_json(args) -> Path:
    root = Path(args.root).expanduser().resolve()
    prepared_dir = Path(args.prepared_dir).expanduser()
    if not prepared_dir.is_absolute():
        prepared_dir = root / prepared_dir

    output_dir = Path(args.output_dir).expanduser()
    if not output_dir.is_absolute():
        output_dir = root / output_dir

    base_summary_path = output_dir / "sync_and_extrinsics_bundle_h170_fixed.json"
    base_summary = safe_load_json(base_summary_path)
    if base_summary is None:
        raise FileNotFoundError(f"CasCalib result JSON not found: {base_summary_path}")

    final = {
        "result_type": "cascalib_final_with_intrinsics_extrinsics_sync",
        "source_summary_json": str(base_summary_path),
        "prepared_dir": str(prepared_dir),
        "output_dir": str(output_dir),
        "base_summary": base_summary,
        "cameras": [],
    }

    for cam_idx in range(1, args.num_cameras + 1):
        cam_name = f"Camera{cam_idx}"
        cam_meta_path = prepared_dir / "metadata" / f"{cam_name}_meta.json"
        cam_result_path = output_dir / cam_name / "bundle_adjusted_result_h170_fixed.json"

        cam_meta = safe_load_json(cam_meta_path) or {}
        cam_result = safe_load_json(cam_result_path) or {}

        final["cameras"].append({
            "camera": cam_name,
            "source_image_dir": cam_meta.get("source_dir"),
            "prepared_undistorted_dir": cam_meta.get("undistorted_dir"),
            "frame_map_csv": cam_meta.get("frame_map_csv"),
            "num_frames": cam_meta.get("num_frames"),
            "image_size": cam_meta.get("image_size"),
            "intrinsics": {
                "note": "camera_matrix is the K stored in prepared metadata. In no-ChArUco mode it is only a synthetic bootstrap K; use bundle_adjusted.intrinsics/single_view estimated K for CasCalib outputs.",
                "camera_matrix": cam_meta.get("camera_matrix"),
                "dist_coeffs": cam_meta.get("dist_coeffs"),
                "undistortion_applied": cam_meta.get("undistortion_applied"),
                "original_intrinsics": cam_meta.get("original_intrinsics"),
            },
            "delay_relative_to_Camera1": cam_result.get("delay_relative_to_Camera1"),
            "prebundle": cam_result.get("prebundle"),
            "bundle_adjusted": cam_result.get("bundle_adjusted"),
            "sync_frame_matches_csv": cam_result.get("sync_frame_matches_csv"),
            "sync_frame_match_example": cam_result.get("sync_frame_match_example"),
            "single_view_info": cam_result.get("single_view_info"),
            "per_camera_result_json": str(cam_result_path),
            "metadata_json": str(cam_meta_path),
            "warnings": cam_meta.get("warnings", []),
        })

    out_path = output_dir / "final_cascalib_with_intrinsics_extrinsics_sync.json"
    write_json(out_path, final)
    print(f"[merge] final JSON: {out_path}")
    return out_path


# -----------------------------------------------------------------------------
# Main
# -----------------------------------------------------------------------------

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default="/home/curica/capstone_real", help="Project root. Default: /home/curica/capstone_real")
    ap.add_argument("--input_root", default="rotated_10fps", help="Raw rotated image root under --root")
    ap.add_argument("--intrinsics_json", default="calibration_in/all_cameras_intrinsics.json", help="Optional all_cameras_intrinsics.json path. Not used unless --use_charuco_intrinsics is set.")
    ap.add_argument("--use_charuco_intrinsics", action="store_true", help="Use ChArUco intrinsics and optional undistortion. Default is OFF in this no-ChArUco script.")
    ap.add_argument("--synthetic_focal_scale", type=float, default=1.0, help="Synthetic bootstrap focal = max(width,height)*scale when ChArUco is disabled")
    ap.add_argument("--prepared_dir", default="data/prepared_rotated_10fps_no_charuco", help="Prepared CasCalib input folder")
    ap.add_argument("--camera1_dir", default=None, help="Optional explicit Camera1 raw image folder")
    ap.add_argument("--camera2_dir", default=None, help="Optional explicit Camera2 raw image folder")
    ap.add_argument("--camera3_dir", default=None, help="Optional explicit Camera3 raw image folder")
    ap.add_argument("--camera4_dir", default=None, help="Optional explicit Camera4 raw image folder")
    ap.add_argument("--camera5_dir", default=None, help="Optional explicit Camera5 raw image folder")
    ap.add_argument("--camera6_dir", default=None, help="Optional explicit Camera6 raw image folder")
    ap.add_argument("--force", action="store_true", help="Overwrite prepared images if they already exist")
    ap.add_argument("--skip_prepare", action="store_true", help="Skip raw image preparation and use --prepared_dir directly")
    ap.add_argument("--prepare_only", action="store_true", help="Only create prepared_dir and stop")
    ap.add_argument("--no_undistort", action="store_true", help="Copy images without undistortion. Forced ON unless --use_charuco_intrinsics is set.")
    ap.add_argument("--undistort_alpha", type=float, default=0.0, help="OpenCV undistort alpha: 0=crop invalid borders, 1=keep all pixels")
    ap.add_argument("--fps_fallback", type=float, default=10.0, help="Fallback FPS when metadata/filename timestamps are unavailable")
    ap.add_argument("--mmpose_dir", default=None, help="Folder containing Camera1.json, Camera2.json, ... If missing, auto MMPose can generate them.")
    ap.add_argument("--cascalib_repo", default=None, help="Local CasCalib clone path. Default: <root>/CasCalib")
    ap.add_argument("--num_cameras", type=int, default=3)
    ap.add_argument("--detector_type", type=int, default=0, choices=[0, 1], help="0=coco mmpose, 1=alphapose")
    ap.add_argument("--output_dir", default="data/cascalib_results_rotated_10fps_no_charuco")
    ap.add_argument("--person_height_m", type=float, default=1.7, help="Assumed human height in meters for CasCalib metric scale")
    ap.add_argument("--match_topk", type=int, default=20, help="Top-k farthest-point matched persons used per camera pair for BA")
    ap.add_argument("--bundle_iterations", type=int, default=200)
    ap.add_argument("--focal_lr", type=float, default=0.1)
    ap.add_argument("--rot_lr", type=float, default=0.1)
    ap.add_argument("--translation_lr", type=float, default=0.1)
    ap.add_argument("--w0", type=float, default=1.0)
    ap.add_argument("--w1", type=float, default=10.0)
    ap.add_argument("--w2", type=float, default=10.0)
    ap.add_argument("--w3", type=float, default=0.1)
    ap.add_argument("--disable_bundle_plots", action="store_true")
    ap.add_argument("--disable_single_view_plots", action="store_true")
    ap.add_argument(
        "--single_view_k_policy",
        choices=["auto", "known", "estimated"],
        default="estimated",
        help="How to choose K downstream from single-view init: auto/known/estimated. Default estimated avoids using ChArUco K.",
    )
    ap.add_argument(
        "--focal_guard_ratio",
        type=float,
        default=999.0,
        help="Warn / auto-fallback threshold for CasCalib focal mismatch. In no-ChArUco mode this is intentionally very large because the bootstrap K is synthetic.",
    )
    ap.add_argument(
        "--fail_on_large_focal_mismatch",
        action="store_true",
        help="Abort instead of warning/fallback when focal mismatch exceeds --focal_guard_ratio",
    )

    # auto MMPose options
    ap.add_argument("--auto_mmpose", dest="auto_mmpose", action="store_true", help="Automatically run MMPose if CameraN.json is missing")
    ap.add_argument("--no_auto_mmpose", dest="auto_mmpose", action="store_false", help="Disable automatic MMPose generation")
    ap.set_defaults(auto_mmpose=True)
    ap.add_argument("--mmpose_pose2d", default="human", help="MMPoseInferencer pose2d model alias/config")
    ap.add_argument("--mmpose_pose2d_weights", default=None, help="Optional pose2d checkpoint path")
    ap.add_argument("--mmpose_det_model", default=None, help="Optional detector model alias/config")
    ap.add_argument("--mmpose_det_weights", default=None, help="Optional detector checkpoint path")
    ap.add_argument("--mmpose_device", default="cuda:0", help="Device for MMPoseInferencer, e.g. cuda:0 or cpu")
    ap.add_argument("--mmpose_bbox_thr", type=float, default=0.3)
    ap.add_argument("--mmpose_kpt_thr", type=float, default=0.0)
    ap.add_argument("--save_mmpose_vis", action="store_true", help="Save MMPose keypoint visualization images before continuing CasCalib")
    ap.add_argument("--mmpose_vis_dir", default=None, help="Optional output folder for MMPose visualization images")
    ap.add_argument("--mmpose_vis_every", type=int, default=1, help="Save one MMPose visualization every N frames")
    ap.add_argument("--mmpose_vis_max_per_camera", type=int, default=0, help="Maximum visualization images per camera; 0 means all sampled frames")
    ap.add_argument("--mmpose_vis_kpt_thr", type=float, default=0.2, help="Keypoint score threshold used only for visualization drawing")
    args = ap.parse_args()

    root = Path(args.root).expanduser().resolve()
    args.root = str(root)

    # Resolve root-relative paths once. This lets the script replace the old
    # prepared_dir-only workflow while keeping --skip_prepare for old prepared data.
    for attr in ["input_root", "intrinsics_json", "prepared_dir", "output_dir", "mmpose_dir"]:
        val = getattr(args, attr, None)
        if val:
            p = Path(val).expanduser()
            if not p.is_absolute():
                setattr(args, attr, str(root / p))
            else:
                setattr(args, attr, str(p))

    args.cascalib_repo = str(find_existing_cascalib_repo(root, args.cascalib_repo))

    if not args.skip_prepare:
        prepare_dataset(args)
    if args.prepare_only:
        print("[done] prepare_only: prepared_dir created at", args.prepared_dir)
        return

    prepared_dir = Path(args.prepared_dir)
    meta_root = prepared_dir / "metadata"
    undist_root = prepared_dir / "undistorted"
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    if args.mmpose_dir:
        mmpose_dir = Path(args.mmpose_dir)
    else:
        mmpose_dir = output_dir / "mmpose_json"

    cascalib_repo = Path(args.cascalib_repo)

    if not prepared_dir.is_dir():
        raise NotADirectoryError(prepared_dir)
    if not meta_root.is_dir() or not undist_root.is_dir():
        raise NotADirectoryError(
            f"Prepared dir must contain metadata/ and undistorted/: {prepared_dir}"
        )
    if not cascalib_repo.is_dir():
        raise NotADirectoryError(cascalib_repo)
    if args.num_cameras < 2:
        raise ValueError("--num_cameras must be >= 2")
    if args.person_height_m <= 0:
        raise ValueError("--person_height_m must be > 0")
    if args.focal_guard_ratio < 0:
        raise ValueError("--focal_guard_ratio must be >= 0")

    validate_cascalib_repo(cascalib_repo)
    data, geometry, multiview_utils, time_align, ICP, util, bundle_adjustment, run_calibration_ransac = load_cascalib_modules(cascalib_repo)

    runtime_hp_path = write_runtime_hyperparameter(cascalib_repo, output_dir, args.person_height_m)

    with open(cascalib_repo / "configuration.json", "r", encoding="utf-8") as f:
        configuration = json.load(f)

    threshold_euc, threshold_cos, angle_filter_video, confidence, termination_cond, num_points, h, iter_n, focal_lr_h, point_lr = util.hyperparameter(
        str(runtime_hp_path)
    )

    warnings_list: List[str] = []
    missing_jsons = [cam_idx for cam_idx in range(1, args.num_cameras + 1) if not (mmpose_dir / f"Camera{cam_idx}.json").exists()]
    if missing_jsons:
        if not args.auto_mmpose:
            raise FileNotFoundError(
                f"Missing MMPose json(s): {[str(mmpose_dir / f'Camera{c}.json') for c in missing_jsons]}"
            )
        print(f"[info] Missing MMPose json for cameras {missing_jsons}. Running automatic MMPose inference...")
        auto_summary = auto_generate_mmpose_jsons(
            undist_root=undist_root,
            mmpose_dir=mmpose_dir,
            num_cameras=args.num_cameras,
            data_module=data,
            multiview_utils_module=multiview_utils,
            detector_type=args.detector_type,
            confidence=confidence,
            args=args,
        )
        save_json(output_dir / "auto_mmpose_summary.json", auto_summary)
        msg = f"[info] Auto MMPose json generated under: {mmpose_dir}"
        print(msg)
        warnings_list.append(msg)
    elif not mmpose_dir.is_dir():
        raise NotADirectoryError(mmpose_dir)
    elif args.save_mmpose_vis:
        vis_summary = render_mmpose_visualizations_from_jsons(
            undist_root=undist_root,
            mmpose_dir=mmpose_dir,
            num_cameras=args.num_cameras,
            args=args,
        )
        save_json(output_dir / "mmpose_visualization_summary.json", vis_summary)
        msg = f"[info] MMPose visualization images saved under: {vis_summary.get('mmpose_vis_dir')}"
        print(msg)
        warnings_list.append(msg)

    run_name = datetime.now().strftime("%Y%m%d_%H%M%S")
    plots_dir = output_dir / f"all_{run_name}"
    (plots_dir / "bundle").mkdir(parents=True, exist_ok=True)

    idx_to_ts_all: List[Dict[int, float]] = []
    image_paths: List[Path] = []
    known_K_all: List[np.ndarray] = []
    first_valid_ts_list: List[float] = []
    data_2d_full_list: List[OrderedDict] = []
    pose_2d_full_list: List[OrderedDict] = []
    datastore_cal_list: List[object] = []

    # ------------------------------------------------------------------
    # Load per-camera metadata and detections
    # ------------------------------------------------------------------
    for cam_idx in range(1, args.num_cameras + 1):
        meta = load_meta(meta_root, cam_idx)
        if meta.get("status") != "ok":
            raise RuntimeError(f"Camera{cam_idx} metadata missing or not ready")

        idx_to_ts, idx_to_name = load_frame_map(meta_root, cam_idx)
        idx_to_ts_all.append(idx_to_ts)
        image_paths.append(undist_root / f"Camera{cam_idx}")

        K = np.array(meta["camera_matrix"], dtype=np.float64)
        known_K_all.append(K)

        json_path = mmpose_dir / f"Camera{cam_idx}.json"
        if not json_path.exists():
            raise FileNotFoundError(f"Missing MMPose json after auto step: {json_path}")
        with open(json_path, "r", encoding="utf-8") as f:
            points_2d = json.load(f)

        if args.detector_type == 0:
            datastore_cal = data.coco_mmpose_dataloader(points_2d)
            datastore = data.coco_mmpose_dataloader(points_2d)
        else:
            datastore_cal = data.alphapose_dataloader(points_2d)
            datastore = data.alphapose_dataloader(points_2d)

        data_2d_full = normalize_key_dict(multiview_utils.get_ankles_heads_dictionary(datastore, cond_tol=confidence))
        pose_2d_full = build_pose_dictionary_for_bundle_adjustment(datastore, cond_tol=confidence)
        if len(data_2d_full) == 0:
            raise RuntimeError(f"Camera{cam_idx}: no valid person detections after parsing {json_path}")
        if len(pose_2d_full) == 0:
            raise RuntimeError(
                f"Camera{cam_idx}: no valid full-pose detections for bundle adjustment after parsing {json_path}. "
                f"Try lowering --mmpose_bbox_thr / --mmpose_kpt_thr or inspect --save_mmpose_vis outputs."
            )

        datastore_cal_list.append(datastore_cal)
        data_2d_full_list.append(data_2d_full)
        pose_2d_full_list.append(pose_2d_full)
        first_valid_ts_list.append(first_valid_ts_from_data(data_2d_full, idx_to_ts))

    common_start_ts = max(first_valid_ts_list)

    plane_matrix_array = []
    plane_dict_array = []
    save_dict_array = []
    pose_2d_array = []
    per_camera_meta = []

    # ------------------------------------------------------------------
    # Single-view initialization per camera
    # ------------------------------------------------------------------
    for cam_zero, cam_idx in enumerate(range(1, args.num_cameras + 1)):
        idx_to_ts = idx_to_ts_all[cam_zero]
        img_dir = image_paths[cam_zero]
        known_K = known_K_all[cam_zero]

        data_2d_trim = filter_dict_by_start_ts(data_2d_full_list[cam_zero], idx_to_ts, common_start_ts)
        pose_2d_trim = filter_dict_by_start_ts(pose_2d_full_list[cam_zero], idx_to_ts, common_start_ts)
        if len(data_2d_trim) == 0:
            raise RuntimeError(f"Camera{cam_idx}: no valid detections remain after common start trimming")

        rep_idx, rep_frame = representative_frame_from_valid_indices(
            img_dir,
            idx_to_ts,
            valid_indices=data_2d_trim.keys(),
            start_ts=common_start_ts,
        )
        img = mpimg.imread(str(rep_frame))
        datastore_cal = datastore_cal_list[cam_zero]

        ankles, cam_matrix_est, normal, ankleWorld, focal, focal_batch, ransac_focal, datastore_filtered = run_calibration_ransac(
            datastore_cal,
            str(runtime_hp_path),
            img,
            img.shape[1],
            img.shape[0],
            run_name,
            cam_zero,
            skip_frame=configuration["skip_frame"],
            max_len=configuration["max_len"],
            min_size=configuration["min_size"],
            save_dir=str(output_dir),
            plotting_true=not args.disable_single_view_plots,
        )

        cam_matrix_est = np.asarray(cam_matrix_est, dtype=np.float64)
        mismatch_ratio = focal_mismatch_ratio(cam_matrix_est, known_K)
        if mismatch_ratio > args.focal_guard_ratio:
            msg = (
                f"[경고] Camera{cam_idx}: CasCalib estimated focal vs metadata bootstrap focal 차이 "
                f"{mismatch_ratio * 100:.1f}% (threshold={args.focal_guard_ratio * 100:.1f}%)"
            )
            print(msg)
            warnings_list.append(msg)
            if args.fail_on_large_focal_mismatch:
                raise RuntimeError(msg + " --fail_on_large_focal_mismatch enabled")

        k_for_plane, k_source = select_single_view_K(
            known_K=known_K,
            cam_matrix_est=cam_matrix_est,
            mismatch_ratio=mismatch_ratio,
            policy=args.single_view_k_policy,
            threshold=args.focal_guard_ratio,
        )

        if k_source != "known":
            msg = f"[info] Camera{cam_idx}: downstream single-view geometry uses {k_source}."
            print(msg)
            warnings_list.append(msg)

        save_dict = {
            "cam_matrix": np.asarray(k_for_plane, dtype=np.float64),
            "cam_matrix_known": np.asarray(known_K, dtype=np.float64),
            "cam_matrix_estimated": cam_matrix_est,
            "ground_normal": np.asarray(normal, dtype=np.float64),
            "ground_position": np.asarray(ankleWorld, dtype=np.float64),
            "focal_mismatch_ratio": float(mismatch_ratio),
            "k_source": k_source,
        }

        plane_matrix, basis_matrix = geometry.find_plane_matrix(
            save_dict["ground_normal"],
            np.linalg.inv(save_dict["cam_matrix"]),
            save_dict["ground_position"],
            img.shape[1],
            img.shape[0],
        )
        plane_data_2d, _ = geometry.camera_to_plane(
            data_2d_trim,
            save_dict["cam_matrix"],
            plane_matrix,
            save_dict["ground_position"],
            save_dict["ground_normal"],
            img.shape[1],
            img.shape[0],
        )

        plane_matrix_array.append(plane_matrix)
        plane_dict_array.append(plane_data_2d)
        save_dict_array.append(save_dict)
        pose_2d_array.append(pose_2d_trim)
        per_camera_meta.append(
            {
                "camera": f"Camera{cam_idx}",
                "representative_frame": str(rep_frame),
                "representative_frame_index": int(rep_idx),
                "representative_frame_timestamp_sec": float(idx_to_ts[rep_idx]),
                "representative_strategy": "median_timestamp_after_common_start_among_valid_detection_frames",
                "common_start_ts": float(common_start_ts),
                "num_pose_frames_after_trim": len(pose_2d_trim),
                "num_ankle_frames_after_trim": len(data_2d_trim),
                "person_height_assumption_m": float(args.person_height_m),
                "focal_mismatch_ratio": float(mismatch_ratio),
                "single_view_k_policy": args.single_view_k_policy,
                "single_view_k_used": k_source,
                "known_fx": float(known_K[0, 0]),
                "known_fy": float(known_K[1, 1]),
                "estimated_fx": float(cam_matrix_est[0, 0]),
                "estimated_fy": float(cam_matrix_est[1, 1]),
            }
        )

    # ------------------------------------------------------------------
    # Temporal sync
    # ------------------------------------------------------------------
    best_shift_array, best_scale_array, sync_dict_array_time = time_align.time_all(
        plane_dict_array[0],
        plane_dict_array[1:],
        save_dir=str(plots_dir),
        sync=True,
        name="temporal",
        window=1,
        dilation=1,
    )

    expected_sync_len = args.num_cameras - 1
    sync_dict_array_time = sanitize_sync_dict_array(sync_dict_array_time, expected_sync_len)

    # ------------------------------------------------------------------
    # ICP with sync_dict duplication guard
    # ------------------------------------------------------------------
    sync_dict_array_input = copy.deepcopy(sync_dict_array_time)
    (
        icp_rot_array,
        icp_init_rot_array,
        icp_shift_array,
        init_ref_center_array,
        init_sync_center_array,
        time_shift_array,
        time_scale_array,
        sync_dict_array_icp,
        index_array,
    ) = ICP.icp(
        plane_dict_array[0],
        plane_dict_array[1:],
        best_shift_array,
        best_scale_array,
        sync_dict_array_input,
        save_dir=str(plots_dir),
        name="_",
    )

    if len(sync_dict_array_icp) != expected_sync_len:
        msg = (
            f"[경고] ICP returned sync_dict_array length {len(sync_dict_array_icp)} "
            f"(expected {expected_sync_len}). Trimming to expected length."
        )
        print(msg)
        warnings_list.append(msg)
    sync_dict_array_clean = sanitize_sync_dict_array(sync_dict_array_icp, expected_sync_len)

    # ------------------------------------------------------------------
    # Pre-bundle camera parameters from CasCalib ICP outputs
    # ------------------------------------------------------------------
    pre_bundle_cal_array = []
    single_view_cal_array = []
    cam_intrinsics_array = []
    pose_2d_array_comb = []
    cam_axis_pre = []
    cam_position_pre = []

    ref_indices = [list(sync_dict_array_clean[s].keys()) for s in range(len(sync_dict_array_clean))]
    ref_intersection = set(ref_indices[0]).intersection(*map(set, ref_indices[1:])) if ref_indices else set()
    indices_array = [sorted(ref_intersection)]
    for s in range(len(sync_dict_array_clean)):
        sync_indices = []
        for i in sorted(ref_intersection):
            sync_indices.append(sync_dict_array_clean[s][i])
        indices_array.append(sync_indices)

    for i in range(0, len(plane_matrix_array)):
        plane_matrix = plane_matrix_array[i]
        if i == 0:
            init_ref_shift_matrix = np.array([init_ref_center_array[0][0], init_ref_center_array[0][1]])
            init_sync_shift_matrix = np.array([init_ref_center_array[0][0], init_ref_center_array[0][1]])
            icp_rot_matrix = np.eye(2)
            init_rot_matrix = np.eye(2)
        else:
            init_ref_shift_matrix = np.asarray(init_ref_center_array[i - 1])
            init_sync_shift_matrix = np.asarray(init_sync_center_array[i - 1])
            icp_rot_matrix = np.asarray(icp_rot_array[i - 1])
            init_rot_matrix = np.asarray(icp_init_rot_array[i - 1])

        init_rot_matrix = np.array([
            [init_rot_matrix[0][0], init_rot_matrix[0][1], 0],
            [init_rot_matrix[1][0], init_rot_matrix[1][1], 0],
            [0, 0, 1],
        ])
        icp_rot_matrix = np.array([
            [icp_rot_matrix[0][0], icp_rot_matrix[0][1], 0],
            [icp_rot_matrix[1][0], icp_rot_matrix[1][1], 0],
            [0, 0, 1],
        ])

        peturb_single_view = {
            "cam_matrix": save_dict_array[i]["cam_matrix"],
            "ground_position": save_dict_array[i]["ground_position"],
            "ground_normal": save_dict_array[i]["ground_normal"],
        }
        peturb_extrinsics = {
            "init_sync_center_array": init_ref_shift_matrix,
            "icp_rot_array": icp_rot_matrix,
            "icp_init_rot_array": init_rot_matrix,
            "plane_matrix_array": plane_matrix,
        }
        pre_bundle_cal_array.append(peturb_extrinsics)
        single_view_cal_array.append(peturb_single_view)

        T01 = np.array([0.0, 0.0, 0.0])
        R01 = icp_rot_matrix @ init_rot_matrix @ plane_matrix[:3, :3]
        t01_shift = T01 + np.array([init_ref_shift_matrix[0], init_ref_shift_matrix[1], 0.0])
        t01_rot_shift = np.linalg.norm(np.array([init_sync_shift_matrix[0], init_sync_shift_matrix[1], 0.0])) * (
            init_rot_matrix @ np.array([0.0, 1.0, 0.0])
        )
        t01_rot_rot_shift = (icp_rot_matrix @ t01_rot_shift) + t01_shift
        sync_position = -1.0 * (
            t01_rot_rot_shift
            - np.array([init_sync_shift_matrix[0], init_sync_shift_matrix[1], 0.0])
            - plane_matrix[:3, 3]
        )

        cam_axis_pre.append(np.transpose(R01))
        cam_position_pre.append(sync_position)
        cam_intrinsics_array.append(save_dict_array[i]["cam_matrix"])

        pose_comb = {}
        for ia in indices_array[i]:
            if ia in pose_2d_array[i]:
                pose_comb[ia] = pose_2d_array[i][ia]
        pose_2d_array_comb.append(pose_comb)

    # ------------------------------------------------------------------
    # Bundle adjustment
    # ------------------------------------------------------------------
    matched_points = bundle_adjustment.match_3d_plotly_input2d_farthest_point(
        pre_bundle_cal_array,
        single_view_cal_array,
        pose_2d_array_comb,
        k=args.match_topk,
    )
    matched_pair_group_count = len(matched_points)

    distortion_k_array: List[np.ndarray] = []
    distortion_p_array: List[np.ndarray] = []
    bundle_rot, bundle_pos, bundle_K = bundle_adjustment.bundle_adjustment(
        matched_points,
        cam_axis_pre,
        cam_position_pre,
        cam_intrinsics_array,
        h,
        distortion_k_array,
        distortion_p_array,
        iteration=args.bundle_iterations,
        save_dir=str(plots_dir / "bundle"),
        run_name=run_name,
        focal_lr=args.focal_lr,
        rot_lr=args.rot_lr,
        translation_lr=args.translation_lr,
        w0=args.w0,
        w1=args.w1,
        w2=args.w2,
        w3=args.w3,
        plot_true=not args.disable_bundle_plots,
    )

    # ------------------------------------------------------------------
    # Save results
    # ------------------------------------------------------------------
    cameras_json = []
    ref_ts = idx_to_ts_all[0]
    sync_match_dir = output_dir / "sync_frame_matches"
    sync_match_dir.mkdir(parents=True, exist_ok=True)

    for i in range(args.num_cameras):
        cam_idx = i + 1
        cam_dir = output_dir / f"Camera{cam_idx}"
        cam_dir.mkdir(parents=True, exist_ok=True)

        R_pre = np.asarray(cam_axis_pre[i], dtype=np.float64)
        C_pre = np.asarray(cam_position_pre[i], dtype=np.float64)
        K_pre = np.asarray(cam_intrinsics_array[i], dtype=np.float64)
        P_pre = make_projection_matrix(K_pre, R_pre, C_pre)

        R_ba = np.asarray(bundle_rot[i], dtype=np.float64)
        C_ba = np.asarray(bundle_pos[i], dtype=np.float64)
        K_ba = np.asarray(bundle_K[i], dtype=np.float64)
        P_ba = make_projection_matrix(K_ba, R_ba, C_ba)

        sync_csv_path = None
        if i == 0:
            delay_frames = 0
            delay_scale = 1.0
            delay_sec = 0.0
            sync_matches = []
        else:
            delay_frames = int(best_shift_array[i - 1])
            delay_scale = float(best_scale_array[i - 1])
            sync_pairs = sync_dict_array_clean[i - 1]
            sync_matches = []
            diffs = []
            cam_ts = idx_to_ts_all[i]
            for ref_idx, sync_idx in sync_pairs.items():
                ref_idx_i = int(ref_idx)
                sync_idx_i = int(sync_idx)
                if ref_idx_i in ref_ts and sync_idx_i in cam_ts:
                    dt = cam_ts[sync_idx_i] - ref_ts[ref_idx_i]
                    diffs.append(dt)
                    sync_matches.append({
                        "ref_frame": ref_idx_i,
                        f"cam{cam_idx}_frame": sync_idx_i,
                        "dt_sec": dt,
                    })
            delay_sec = float(np.median(diffs)) if diffs else float(delay_frames * median_frame_dt(idx_to_ts_all[i]))

            sync_csv_path = sync_match_dir / f"Camera{cam_idx}_to_Camera1_matches.csv"
            with open(sync_csv_path, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow([
                    "ref_camera",
                    "ref_frame",
                    "ref_timestamp_sec",
                    "sync_camera",
                    "sync_frame",
                    "sync_timestamp_sec",
                    "dt_sec",
                    "frame_delta",
                ])
                for m in sync_matches:
                    ref_frame = int(m["ref_frame"])
                    sync_frame = int(m[f"cam{cam_idx}_frame"])
                    writer.writerow([
                        "Camera1",
                        ref_frame,
                        ref_ts.get(ref_frame, ""),
                        f"Camera{cam_idx}",
                        sync_frame,
                        cam_ts.get(sync_frame, ""),
                        float(m["dt_sec"]),
                        int(sync_frame - ref_frame),
                    ])

        np.savez(
            cam_dir / "prebundle_extrinsics.npz",
            R_world_to_camera=R_pre,
            camera_center_world=C_pre,
            projection_matrix=P_pre,
            K=K_pre,
            delay_sec=np.float64(delay_sec),
            frame_shift=np.int32(delay_frames),
            time_scale=np.float64(delay_scale),
            person_height_assumption_m=np.float64(args.person_height_m),
        )
        np.savez(
            cam_dir / "bundle_adjusted_extrinsics_h170_fixed.npz",
            R_world_to_camera=R_ba,
            camera_center_world=C_ba,
            projection_matrix=P_ba,
            K=K_ba,
            delay_sec=np.float64(delay_sec),
            frame_shift=np.int32(delay_frames),
            time_scale=np.float64(delay_scale),
            person_height_assumption_m=np.float64(args.person_height_m),
        )
        np.savez(
            cam_dir / "bundle_adjusted_intrinsics_h170_fixed.npz",
            K=K_ba,
            person_height_assumption_m=np.float64(args.person_height_m),
        )

        save_json(
            cam_dir / "bundle_adjusted_result_h170_fixed.json",
            {
                "camera": f"Camera{cam_idx}",
                "person_height_assumption_m": float(args.person_height_m),
                "metric_scale_note": "World scale is interpreted in meters under the assumed human height prior.",
                "single_view_info": per_camera_meta[i],
                "delay_relative_to_Camera1": {
                    "frame_shift": delay_frames,
                    "time_scale": delay_scale,
                    "approx_seconds": delay_sec,
                },
                "prebundle": {
                    "rotation_world_to_camera": R_pre.tolist(),
                    "camera_center_world": C_pre.tolist(),
                    "projection_matrix": P_pre.tolist(),
                    "intrinsics": K_pre.tolist(),
                },
                "bundle_adjusted": {
                    "rotation_world_to_camera": R_ba.tolist(),
                    "camera_center_world": C_ba.tolist(),
                    "projection_matrix": P_ba.tolist(),
                    "intrinsics": K_ba.tolist(),
                },
                "num_sync_matches_used_for_delay_summary": len(sync_matches),
                "sync_frame_matches_csv": str(sync_csv_path) if sync_csv_path is not None else None,
                "sync_frame_match_example": sync_matches[:10],
            },
        )

        cameras_json.append(
            {
                "camera": f"Camera{cam_idx}",
                "single_view": per_camera_meta[i],
                "delay_relative_to_Camera1": {
                    "frame_shift": delay_frames,
                    "time_scale": delay_scale,
                    "approx_seconds": delay_sec,
                },
                "sync_frame_matches_csv": str(sync_csv_path) if sync_csv_path is not None else None,
                "sync_frame_match_example": sync_matches[:10],
                "prebundle_extrinsics": {
                    "rotation_world_to_camera": R_pre.tolist(),
                    "camera_center_world": C_pre.tolist(),
                },
                "bundle_adjusted_extrinsics": {
                    "rotation_world_to_camera": R_ba.tolist(),
                    "camera_center_world": C_ba.tolist(),
                },
            }
        )

    summary = {
        "run_name": run_name,
        "num_cameras": args.num_cameras,
        "detector_type": args.detector_type,
        "person_height_assumption_m": float(args.person_height_m),
        "runtime_hyperparameter_json": str(runtime_hp_path),
        "matched_pair_group_count": matched_pair_group_count,
        "single_view_k_policy": args.single_view_k_policy,
        "focal_guard_ratio": float(args.focal_guard_ratio),
        "mmpose_dir": str(mmpose_dir),
        "sync_frame_matches_dir": str(sync_match_dir),
        "warnings": warnings_list,
        "cameras": cameras_json,
    }
    save_json(output_dir / "sync_and_extrinsics_bundle_h170_fixed.json", summary)
    final_json_path = merge_final_json(args)

    print("\n[done] Saved fixed CasCalib bundle-adjusted results to:")
    print(output_dir.resolve())
    print("[done] Final merged JSON:")
    print(final_json_path.resolve())


if __name__ == "__main__":
    main()
