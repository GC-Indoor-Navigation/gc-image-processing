#!/usr/bin/env python3
"""Run the offline camera calibration pipeline as a single job entrypoint."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List


REPO_ROOT = Path(__file__).resolve().parents[2]
CALIBRATION_ROOT = REPO_ROOT / "tools" / "calibration"
SYNC_SCRIPT = CALIBRATION_ROOT / "sync_frames" / "sync_by_camera1_nearest_common_range.py"
CASCALIB_SCRIPT = CALIBRATION_ROOT / "cascalib_runner" / "run_cascalib_rotation_no_charuco.py"
CASCALIB_REPO = CALIBRATION_ROOT / "vendor" / "CasCalib"


class PipelineError(RuntimeError):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def camera_input_dir(input_dir: Path, camera_index: int) -> Path:
    return input_dir / f"camera{camera_index}" / f"camera{camera_index}_10fps"


def validate_input(input_dir: Path, num_cameras: int) -> None:
    if not input_dir.is_dir():
        raise PipelineError(f"Input directory does not exist: {input_dir}")

    missing: List[str] = []
    empty: List[str] = []
    for cam_idx in range(1, num_cameras + 1):
        cam_dir = camera_input_dir(input_dir, cam_idx)
        if not cam_dir.is_dir():
            missing.append(str(cam_dir))
            continue
        has_image = any(
            p.is_file() and p.suffix.lower() in {".jpg", ".jpeg", ".png", ".bmp", ".webp"}
            for p in cam_dir.iterdir()
        )
        if not has_image:
            empty.append(str(cam_dir))

    if missing:
        raise PipelineError("Missing camera input folders:\n- " + "\n- ".join(missing))
    if empty:
        raise PipelineError("Camera input folders contain no images:\n- " + "\n- ".join(empty))


def run_command(
    *,
    name: str,
    command: List[str],
    cwd: Path,
    log_path: Path,
    dry_run: bool,
) -> Dict[str, Any]:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    record: Dict[str, Any] = {
        "name": name,
        "command": command,
        "cwd": str(cwd),
        "log": str(log_path),
        "started_at": utc_now(),
        "finished_at": None,
        "returncode": None,
        "skipped": bool(dry_run),
    }

    if dry_run:
        log_path.write_text("DRY RUN: command was not executed.\n", encoding="utf-8")
        record["finished_at"] = utc_now()
        record["returncode"] = 0
        return record

    with log_path.open("w", encoding="utf-8") as log_file:
        log_file.write("$ " + " ".join(command) + "\n\n")
        log_file.flush()
        completed = subprocess.run(
            command,
            cwd=str(cwd),
            stdout=log_file,
            stderr=subprocess.STDOUT,
            text=True,
            check=False,
        )

    record["finished_at"] = utc_now()
    record["returncode"] = int(completed.returncode)
    if completed.returncode != 0:
        raise PipelineError(f"{name} failed with exit code {completed.returncode}. See {log_path}")
    return record


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Offline calibration pipeline job runner")
    parser.add_argument("--input", required=True, type=Path, help="Mounted input session directory")
    parser.add_argument("--output", required=True, type=Path, help="Mounted output directory")
    parser.add_argument("--logs", required=True, type=Path, help="Mounted log directory")
    parser.add_argument("--num-cameras", type=int, default=3)
    parser.add_argument("--person-height-m", type=float, default=1.7)
    parser.add_argument("--max-dt-ms", type=float, default=80.0)
    parser.add_argument("--mmpose-device", default="cuda:0")
    parser.add_argument("--mmpose-dir", type=Path, default=None, help="Optional existing MMPose JSON directory")
    parser.add_argument("--no-auto-mmpose", action="store_true")
    parser.add_argument("--skip-sync", action="store_true", help="Use input as the already-synced image root")
    parser.add_argument("--skip-cascalib", action="store_true", help="Stop after input validation / sync")
    parser.add_argument("--dry-run", action="store_true", help="Write commands without executing them")
    parser.add_argument("--force", action="store_true", help="Overwrite prepared/synced outputs")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    input_dir = args.input.resolve()
    output_dir = args.output.resolve()
    logs_dir = args.logs.resolve()

    output_dir.mkdir(parents=True, exist_ok=True)
    logs_dir.mkdir(parents=True, exist_ok=True)

    result_path = output_dir / "job_result.json"
    result: Dict[str, Any] = {
        "status": "RUNNING",
        "started_at": utc_now(),
        "finished_at": None,
        "input_dir": str(input_dir),
        "output_dir": str(output_dir),
        "logs_dir": str(logs_dir),
        "final_json": None,
        "steps": [],
        "warnings": [],
        "error": None,
    }
    write_json(result_path, result)

    try:
        if args.num_cameras != 3:
            raise PipelineError("The current sync script supports exactly 3 cameras.")
        validate_input(input_dir, args.num_cameras)

        synced_dir = output_dir / "synced"
        prepared_dir = output_dir / "prepared"
        cascalib_output_dir = output_dir / "cascalib"

        if args.skip_sync:
            synced_root = input_dir
            result["warnings"].append("--skip-sync enabled: using input directory as synced root.")
        else:
            sync_command = [
                sys.executable,
                str(SYNC_SCRIPT),
                "--root",
                str(input_dir),
                "--out_dir",
                str(synced_dir),
                "--max-dt-ms",
                str(args.max_dt_ms),
                "--mode",
                "copy",
            ]
            if args.force:
                sync_command.append("--overwrite")
            step = run_command(
                name="timestamp_sync",
                command=sync_command,
                cwd=REPO_ROOT,
                log_path=logs_dir / "01_timestamp_sync.log",
                dry_run=args.dry_run,
            )
            result["steps"].append(step)
            write_json(result_path, result)
            synced_root = synced_dir

        if args.skip_cascalib:
            result["status"] = "SUCCEEDED"
            result["finished_at"] = utc_now()
            result["warnings"].append("--skip-cascalib enabled: final calibration JSON was not generated.")
            write_json(result_path, result)
            return 0

        cascalib_command = [
            sys.executable,
            str(CASCALIB_SCRIPT),
            "--root",
            str(output_dir),
            "--input_root",
            str(synced_root),
            "--prepared_dir",
            str(prepared_dir),
            "--output_dir",
            str(cascalib_output_dir),
            "--cascalib_repo",
            str(CASCALIB_REPO),
            "--num_cameras",
            str(args.num_cameras),
            "--person_height_m",
            str(args.person_height_m),
            "--mmpose_device",
            str(args.mmpose_device),
        ]
        if args.force:
            cascalib_command.append("--force")
        if args.no_auto_mmpose:
            cascalib_command.append("--no_auto_mmpose")
        if args.mmpose_dir is not None:
            cascalib_command.extend(["--mmpose_dir", str(args.mmpose_dir.resolve())])

        step = run_command(
            name="cascalib",
            command=cascalib_command,
            cwd=REPO_ROOT,
            log_path=logs_dir / "02_cascalib.log",
            dry_run=args.dry_run,
        )
        result["steps"].append(step)

        final_json = cascalib_output_dir / "final_cascalib_with_intrinsics_extrinsics_sync.json"
        if not args.dry_run and not final_json.exists():
            raise PipelineError(f"CasCalib completed but final JSON was not found: {final_json}")

        result["status"] = "SUCCEEDED"
        result["finished_at"] = utc_now()
        result["final_json"] = str(final_json)
        write_json(result_path, result)
        return 0

    except Exception as exc:
        result["status"] = "FAILED"
        result["finished_at"] = utc_now()
        result["error"] = str(exc)
        write_json(result_path, result)
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
