import json
from types import SimpleNamespace

import pytest

from app.pipeline.proximity_alerts import (
    DangerPointProximityAlertEvaluator,
    DangerPointProximityConfig,
    load_danger_points,
    parse_valid_joints,
)


def test_load_danger_points_uses_json_defaults(tmp_path):
    path = _write_danger_points(tmp_path, danger_radius=0.2)

    points = load_danger_points(path)

    assert len(points) == 1
    assert points[0].point_id == "obstacle-1"
    assert points[0].center == (0.0, 0.0, 0.0)
    assert points[0].danger_radius_m == 0.2
    assert points[0].approach_warning_radius_m == pytest.approx(0.6)
    assert points[0].approach_danger_radius_m == pytest.approx(0.35)
    assert points[0].collision_warning_radius_m == pytest.approx(0.4)


def test_parse_valid_joints_accepts_live_and_stored_shapes():
    joints = parse_valid_joints(
        {
            "nose": {"xyz": [1, 2, 3]},
            "left_ankle": [0.1, 0.2, 0.3],
            "right_ankle": {"xyz": None},
            "bad": [1, 2],
        }
    )

    assert joints == {
        "nose": (1.0, 2.0, 3.0),
        "left_ankle": (0.1, 0.2, 0.3),
    }


def test_evaluator_returns_collision_danger_for_near_joint(tmp_path):
    evaluator = _evaluator(tmp_path)
    skeleton = _skeleton_result(
        joints_world={
            "left_ankle": {"xyz": [0.1, 0.0, 0.0]},
            "right_ankle": {"xyz": [0.12, 0.0, 0.0]},
            **_filler_joints(6, [2.0, 2.0, 2.0]),
        },
    )

    alert = evaluator.evaluate(
        processing_result=SimpleNamespace(frame_set_id=7),
        skeleton_result=skeleton,
        ttl_ms=500,
        processor_name="MMPoseTriangulationProcessor",
        camera_devices=("android_device_001", "android_device_002"),
    )

    assert alert is not None
    assert alert.event_id == "danger-point-7-obstacle-1-collision_danger"
    assert alert.frame_set_id == 7
    assert alert.severity == "danger"
    assert alert.obstacle_id == "obstacle-1"
    assert alert.joint == "left_ankle"
    assert alert.distance_m == pytest.approx(0.1)
    assert alert.ttl_ms == 500
    assert alert.source.camera_devices == (
        "android_device_001",
        "android_device_002",
    )


def test_evaluator_returns_approach_warning_for_person_xy(tmp_path):
    evaluator = _evaluator(tmp_path)
    skeleton = _skeleton_result(
        joints_world={
            "left_ankle": {"xyz": [0.5, 0.0, 2.0]},
            "right_ankle": {"xyz": [0.5, 0.0, 2.0]},
            **_filler_joints(6, [2.0, 2.0, 2.0]),
        },
    )

    alert = evaluator.evaluate(
        processing_result=SimpleNamespace(frame_set_id=7),
        skeleton_result=skeleton,
        ttl_ms=500,
        processor_name="MMPoseTriangulationProcessor",
        camera_devices=("android_device_001",),
    )

    assert alert is not None
    assert alert.event_id == "danger-point-7-obstacle-1-approach_warning"
    assert alert.severity == "warning"
    assert alert.joint is None
    assert alert.distance_m == pytest.approx(0.5)


def test_evaluator_returns_none_for_far_person(tmp_path):
    evaluator = _evaluator(tmp_path)

    alert = evaluator.evaluate(
        processing_result=SimpleNamespace(frame_set_id=7),
        skeleton_result=_skeleton_result(
            joints_world={
                "left_ankle": {"xyz": [2.0, 0.0, 2.0]},
                "right_ankle": {"xyz": [2.0, 0.0, 2.0]},
                **_filler_joints(6, [2.0, 2.0, 2.0]),
            },
        ),
        ttl_ms=500,
        processor_name="MMPoseTriangulationProcessor",
        camera_devices=("android_device_001",),
    )

    assert alert is None


def test_evaluator_filters_low_valid_joint_count(tmp_path):
    evaluator = _evaluator(tmp_path, min_valid_joints=8)

    alert = evaluator.evaluate(
        processing_result=SimpleNamespace(frame_set_id=7),
        skeleton_result=_skeleton_result(
            num_valid_joints=2,
            joints_world={
                "left_ankle": {"xyz": [0.1, 0.0, 0.0]},
                "right_ankle": {"xyz": [0.1, 0.0, 0.0]},
            },
        ),
        ttl_ms=500,
        processor_name="MMPoseTriangulationProcessor",
        camera_devices=("android_device_001",),
    )

    assert alert is None


def test_evaluator_filters_high_reprojection_error(tmp_path):
    evaluator = _evaluator(tmp_path, max_avg_reproj_error_px=10.0)

    alert = evaluator.evaluate(
        processing_result=SimpleNamespace(frame_set_id=7),
        skeleton_result=_skeleton_result(
            avg_reproj_error_px=20.0,
            joints_world={
                "left_ankle": {"xyz": [0.1, 0.0, 0.0]},
                "right_ankle": {"xyz": [0.1, 0.0, 0.0]},
                **_filler_joints(6, [2.0, 2.0, 2.0]),
            },
        ),
        ttl_ms=500,
        processor_name="MMPoseTriangulationProcessor",
        camera_devices=("android_device_001",),
    )

    assert alert is None


def _evaluator(
    tmp_path,
    *,
    min_valid_joints=8,
    max_avg_reproj_error_px=80.0,
):
    return DangerPointProximityAlertEvaluator(
        DangerPointProximityConfig(
            danger_points_json=_write_danger_points(tmp_path),
            min_valid_joints=min_valid_joints,
            max_avg_reproj_error_px=max_avg_reproj_error_px,
            smooth_alpha=1.0,
        )
    )


def _write_danger_points(tmp_path, *, danger_radius=0.2):
    path = tmp_path / "danger_points.json"
    path.write_text(
        json.dumps(
            {
                "coordinate_frame": "CasCalib world",
                "danger_radius": danger_radius,
                "points": [
                    {
                        "point_id": "obstacle-1",
                        "world_x": 0.0,
                        "world_y": 0.0,
                        "world_z": 0.0,
                        "danger_radius": danger_radius,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return path


def _skeleton_result(
    *,
    frame_set_id=7,
    anchor_timestamp_ms=1000,
    num_valid_joints=8,
    avg_reproj_error_px=1.0,
    joints_world,
):
    return {
        "frame_set_id": frame_set_id,
        "anchor_timestamp_ms": anchor_timestamp_ms,
        "num_valid_joints": num_valid_joints,
        "avg_reproj_error_px": avg_reproj_error_px,
        "joints_world": joints_world,
    }


def _filler_joints(count, xyz):
    return {f"joint_{index}": {"xyz": xyz} for index in range(count)}
