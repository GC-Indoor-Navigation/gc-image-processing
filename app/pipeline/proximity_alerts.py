import json
import math
from dataclasses import asdict, dataclass, is_dataclass
from pathlib import Path
from typing import Any

from app.pipeline.alerts import AlertEvent, AlertSource


Vec3 = tuple[float, float, float]
Vec2 = tuple[float, float]


@dataclass(frozen=True)
class DangerPoint:
    point_id: str
    center: Vec3
    danger_radius_m: float
    approach_warning_radius_m: float
    approach_danger_radius_m: float
    collision_warning_radius_m: float
    coordinate_frame: str = "CasCalib world"
    source_camera: str = ""


@dataclass(frozen=True)
class DangerPointProximityConfig:
    danger_points_json: Path
    predict_seconds: float = 1.0
    smooth_alpha: float = 0.35
    min_valid_joints: int = 8
    max_avg_reproj_error_px: float = 80.0
    alert_cooldown_sec: float = 0.0
    approach_warning_radius_m: float | None = None
    approach_danger_radius_m: float | None = None
    collision_warning_radius_m: float | None = None


@dataclass
class _PersonState:
    prev_time_sec: float | None = None
    prev_position_xy: Vec2 | None = None
    smooth_position_xy: Vec2 | None = None
    velocity_xy_mps: Vec2 = (0.0, 0.0)


@dataclass(frozen=True)
class _DangerEventCandidate:
    level: str
    frame_set_id: int
    relay_run_id: int | None
    timestamp_ms: int
    danger_point_id: str
    distance_m: float
    joint: str | None
    reason: str
    severity_rank: int


class DangerPointProximityAlertEvaluator:
    def __init__(self, config: DangerPointProximityConfig):
        self.config = config
        self.danger_points = load_danger_points(
            config.danger_points_json,
            approach_warning_radius_m=config.approach_warning_radius_m,
            approach_danger_radius_m=config.approach_danger_radius_m,
            collision_warning_radius_m=config.collision_warning_radius_m,
        )
        self._state = _PersonState()
        self._last_alert_by_key: dict[tuple[str, str], float] = {}

    def evaluate(
        self,
        *,
        processing_result: Any,
        skeleton_result: Any | None,
        ttl_ms: int,
        processor_name: str,
        camera_devices: tuple[str, ...],
    ) -> AlertEvent | None:
        tri = _extract_triangulation_summary(
            skeleton_result=skeleton_result,
            processing_result=processing_result,
        )
        if tri is None:
            return None

        frame_set_id = _to_int(tri.get("frame_set_id"), default=-1)
        timestamp_ms = _to_int(tri.get("anchor_timestamp_ms"), default=0)
        timestamp_sec = _to_seconds(timestamp_ms)
        relay_run_id = _to_optional_int(tri.get("relay_run_id"))
        if relay_run_id is None:
            relay_run_id = _to_optional_int(getattr(processing_result, "relay_run_id", None))

        valid_joint_count = _to_int(tri.get("num_valid_joints"), default=0)
        if valid_joint_count < self.config.min_valid_joints:
            return None

        avg_reproj_error_px = _to_optional_float(tri.get("avg_reproj_error_px"))
        if (
            avg_reproj_error_px is not None
            and avg_reproj_error_px > self.config.max_avg_reproj_error_px
        ):
            return None

        valid_joints = parse_valid_joints(tri.get("joints_world"))
        if not valid_joints:
            return None

        person_xy, _person_source = estimate_person_xy(valid_joints)
        if person_xy is None:
            return None

        smooth_xy = self._smooth_xy(person_xy)
        velocity_xy = self._update_velocity_xy(smooth_xy, timestamp_sec)

        candidates = [
            candidate
            for danger in self.danger_points
            if (
                candidate := self._evaluate_danger_point(
                    danger=danger,
                    frame_set_id=frame_set_id,
                    relay_run_id=relay_run_id,
                    timestamp_ms=timestamp_ms,
                    timestamp_sec=timestamp_sec,
                    valid_joints=valid_joints,
                    person_xy=smooth_xy,
                    velocity_xy=velocity_xy,
                )
            )
            is not None
        ]
        if not candidates:
            return None

        selected = min(candidates, key=lambda item: (-item.severity_rank, item.distance_m))
        return AlertEvent(
            event_id=(
                f"danger-point-{selected.frame_set_id}-"
                f"{selected.danger_point_id}-{selected.level.lower()}"
            ),
            frame_set_id=selected.frame_set_id,
            relay_run_id=selected.relay_run_id,
            timestamp_ms=selected.timestamp_ms,
            severity=_to_alert_severity(selected.level),
            distance_m=selected.distance_m,
            joint=selected.joint,
            obstacle_id=selected.danger_point_id,
            ttl_ms=ttl_ms,
            source=AlertSource(
                processor=processor_name,
                camera_devices=camera_devices,
            ),
        )

    def _evaluate_danger_point(
        self,
        *,
        danger: DangerPoint,
        frame_set_id: int,
        relay_run_id: int | None,
        timestamp_ms: int,
        timestamp_sec: float | None,
        valid_joints: dict[str, Vec3],
        person_xy: Vec2,
        velocity_xy: Vec2,
    ) -> _DangerEventCandidate | None:
        approach_distance_xy = distance_xy(person_xy, danger.center)
        predicted_approach_distance_xy = predicted_min_distance_xy(
            position_xy=person_xy,
            velocity_xy=velocity_xy,
            danger_center=danger.center,
            predict_seconds=self.config.predict_seconds,
        )
        collision_distance_3d, nearest_joint = min_joint_distance_3d(
            valid_joints,
            danger.center,
        )

        level: str | None = None
        distance_m: float | None = None
        joint: str | None = None
        reason = ""
        severity_rank = 0

        if collision_distance_3d <= danger.danger_radius_m:
            level = "COLLISION_DANGER"
            distance_m = collision_distance_3d
            joint = nearest_joint
            reason = "joint within danger radius"
            severity_rank = 4
        elif approach_distance_xy <= danger.approach_danger_radius_m:
            level = "APPROACH_DANGER"
            distance_m = approach_distance_xy
            reason = "person xy within danger radius"
            severity_rank = 3
        elif predicted_approach_distance_xy <= danger.approach_danger_radius_m:
            level = "APPROACH_DANGER"
            distance_m = predicted_approach_distance_xy
            reason = "predicted person xy enters danger radius"
            severity_rank = 3
        elif collision_distance_3d <= danger.collision_warning_radius_m:
            level = "COLLISION_WARNING"
            distance_m = collision_distance_3d
            joint = nearest_joint
            reason = "joint within collision warning radius"
            severity_rank = 2
        elif approach_distance_xy <= danger.approach_warning_radius_m:
            level = "APPROACH_WARNING"
            distance_m = approach_distance_xy
            reason = "person xy within warning radius"
            severity_rank = 1
        elif predicted_approach_distance_xy <= danger.approach_warning_radius_m:
            level = "APPROACH_WARNING"
            distance_m = predicted_approach_distance_xy
            reason = "predicted person xy enters warning radius"
            severity_rank = 1

        if level is None or distance_m is None:
            return None

        candidate = _DangerEventCandidate(
            level=level,
            frame_set_id=frame_set_id,
            relay_run_id=relay_run_id,
            timestamp_ms=timestamp_ms,
            danger_point_id=danger.point_id,
            distance_m=distance_m,
            joint=joint,
            reason=reason,
            severity_rank=severity_rank,
        )
        if not self._passes_cooldown(candidate, timestamp_sec):
            return None
        return candidate

    def _smooth_xy(self, xy: Vec2) -> Vec2:
        previous = self._state.smooth_position_xy
        if previous is None:
            self._state.smooth_position_xy = xy
            return xy

        alpha = max(0.0, min(1.0, self.config.smooth_alpha))
        smoothed = (
            alpha * xy[0] + (1.0 - alpha) * previous[0],
            alpha * xy[1] + (1.0 - alpha) * previous[1],
        )
        self._state.smooth_position_xy = smoothed
        return smoothed

    def _update_velocity_xy(self, xy: Vec2, timestamp_sec: float | None) -> Vec2:
        if timestamp_sec is None:
            return self._state.velocity_xy_mps

        previous_time = self._state.prev_time_sec
        previous_position = self._state.prev_position_xy
        self._state.prev_time_sec = timestamp_sec
        self._state.prev_position_xy = xy

        if previous_time is None or previous_position is None:
            return self._state.velocity_xy_mps

        dt = timestamp_sec - previous_time
        if dt <= 1e-6:
            return self._state.velocity_xy_mps

        velocity = (
            (xy[0] - previous_position[0]) / dt,
            (xy[1] - previous_position[1]) / dt,
        )
        self._state.velocity_xy_mps = velocity
        return velocity

    def _passes_cooldown(
        self,
        candidate: _DangerEventCandidate,
        timestamp_sec: float | None,
    ) -> bool:
        if self.config.alert_cooldown_sec <= 0 or timestamp_sec is None:
            return True

        key = (candidate.danger_point_id, candidate.level)
        last_timestamp = self._last_alert_by_key.get(key)
        if (
            last_timestamp is not None
            and timestamp_sec - last_timestamp < self.config.alert_cooldown_sec
        ):
            return False
        self._last_alert_by_key[key] = timestamp_sec
        return True


def load_danger_points(
    danger_json_path: Path,
    *,
    approach_warning_radius_m: float | None = None,
    approach_danger_radius_m: float | None = None,
    collision_warning_radius_m: float | None = None,
) -> list[DangerPoint]:
    with danger_json_path.open("r", encoding="utf-8") as file:
        data = json.load(file)

    coordinate_frame = str(data.get("coordinate_frame", "CasCalib world"))
    default_danger_radius = float(data.get("danger_radius", 0.2))
    raw_points = data.get("points", [])
    if not isinstance(raw_points, list) or not raw_points:
        raise ValueError(f"No danger points found in {danger_json_path}")

    danger_points: list[DangerPoint] = []
    for index, point in enumerate(raw_points, start=1):
        danger_radius = float(point.get("danger_radius", default_danger_radius))
        danger_points.append(
            DangerPoint(
                point_id=str(point.get("point_id", index)),
                center=(
                    float(point["world_x"]),
                    float(point["world_y"]),
                    float(point["world_z"]),
                ),
                danger_radius_m=danger_radius,
                approach_warning_radius_m=(
                    float(approach_warning_radius_m)
                    if approach_warning_radius_m is not None
                    else max(0.6, danger_radius * 3.0)
                ),
                approach_danger_radius_m=(
                    float(approach_danger_radius_m)
                    if approach_danger_radius_m is not None
                    else max(0.35, danger_radius * 1.75)
                ),
                collision_warning_radius_m=(
                    float(collision_warning_radius_m)
                    if collision_warning_radius_m is not None
                    else max(0.4, danger_radius * 2.0)
                ),
                coordinate_frame=str(point.get("coordinate_frame", coordinate_frame)),
                source_camera=str(point.get("camera", "")),
            )
        )
    return danger_points


def parse_valid_joints(joints_world: Any) -> dict[str, Vec3]:
    if not isinstance(joints_world, dict):
        return {}

    valid: dict[str, Vec3] = {}
    for name, value in joints_world.items():
        xyz = _extract_xyz(value)
        if xyz is not None:
            valid[str(name)] = xyz
    return valid


def estimate_person_xy(valid_joints: dict[str, Vec3]) -> tuple[Vec2 | None, str]:
    if "left_ankle" in valid_joints and "right_ankle" in valid_joints:
        return (
            midpoint_xy(valid_joints["left_ankle"], valid_joints["right_ankle"]),
            "ankle_midpoint",
        )
    if "left_hip" in valid_joints and "right_hip" in valid_joints:
        return (
            midpoint_xy(valid_joints["left_hip"], valid_joints["right_hip"]),
            "hip_midpoint",
        )
    if valid_joints:
        xs = [joint[0] for joint in valid_joints.values()]
        ys = [joint[1] for joint in valid_joints.values()]
        return (sum(xs) / len(xs), sum(ys) / len(ys)), "all_joint_mean"
    return None, "none"


def midpoint_xy(a: Vec3, b: Vec3) -> Vec2:
    return ((a[0] + b[0]) / 2.0, (a[1] + b[1]) / 2.0)


def distance_xy(person_xy: Vec2, danger_center: Vec3) -> float:
    return math.hypot(person_xy[0] - danger_center[0], person_xy[1] - danger_center[1])


def distance_3d(a: Vec3, b: Vec3) -> float:
    return math.sqrt((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2 + (a[2] - b[2]) ** 2)


def min_joint_distance_3d(
    valid_joints: dict[str, Vec3],
    danger_center: Vec3,
) -> tuple[float, str]:
    best_distance = float("inf")
    best_name = ""
    for name, joint in valid_joints.items():
        current = distance_3d(joint, danger_center)
        if current < best_distance:
            best_distance = current
            best_name = name
    return best_distance, best_name


def predicted_min_distance_xy(
    *,
    position_xy: Vec2,
    velocity_xy: Vec2,
    danger_center: Vec3,
    predict_seconds: float,
) -> float:
    px, py = position_xy
    vx, vy = velocity_xy
    cx, cy = danger_center[0], danger_center[1]
    velocity_norm = vx * vx + vy * vy
    if velocity_norm < 1e-12:
        return math.hypot(px - cx, py - cy)

    closest_time = ((cx - px) * vx + (cy - py) * vy) / velocity_norm
    closest_time = max(0.0, min(float(predict_seconds), closest_time))
    predicted_x = px + vx * closest_time
    predicted_y = py + vy * closest_time
    return math.hypot(predicted_x - cx, predicted_y - cy)


def _extract_triangulation_summary(
    *,
    skeleton_result: Any | None,
    processing_result: Any,
) -> dict[str, Any] | None:
    source = _to_jsonable(skeleton_result)
    if isinstance(source, dict):
        if isinstance(source.get("triangulation_summary"), dict):
            return source["triangulation_summary"]
        if isinstance(source.get("processing_result"), dict) and isinstance(
            source["processing_result"].get("triangulation_summary"),
            dict,
        ):
            return source["processing_result"]["triangulation_summary"]
        if isinstance(source.get("joints_world"), dict):
            return source

    fallback = _to_jsonable(processing_result)
    if isinstance(fallback, dict) and isinstance(
        fallback.get("triangulation_summary"),
        dict,
    ):
        return fallback["triangulation_summary"]
    return None


def _extract_xyz(value: Any) -> Vec3 | None:
    if isinstance(value, dict):
        value = value.get("xyz")
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        return None
    try:
        xyz = (float(value[0]), float(value[1]), float(value[2]))
    except (TypeError, ValueError):
        return None
    if not all(math.isfinite(component) for component in xyz):
        return None
    return xyz


def _to_jsonable(value: Any) -> Any:
    if value is None:
        return None
    if is_dataclass(value):
        return asdict(value)
    return value


def _to_seconds(timestamp_ms: int | None) -> float | None:
    if timestamp_ms is None:
        return None
    return float(timestamp_ms) / 1000.0


def _to_alert_severity(level: str):
    if level.endswith("_DANGER"):
        return "danger"
    if level.endswith("_WARNING"):
        return "warning"
    return "info"


def _to_int(value: Any, *, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _to_optional_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _to_optional_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
