#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
camera1 기준 timestamp nearest matching 코드.

핵심 로직:
1. 파일명 앞 timestamp(ms)를 읽음
2. 각 카메라 프레임을 timestamp 오름차순 정렬
3. common_start = max(camera1_start, camera2_start, camera3_start)
4. common_end   = min(camera1_end, camera2_end, camera3_end)
5. 공통 시간 구간 안에서만 camera1 기준 nearest matching
6. camera1, camera2, camera3가 모두 매칭된 triplet만 output 폴더로 복사

원본 폴더는 건드리지 않는다.
"""

import argparse
import csv
import json
import os
import re
import shutil
import statistics
from bisect import bisect_left
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple


IMG_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}


@dataclass
class Frame:
    camera: str
    path: Path
    meta_path: Optional[Path]
    timestamp_ms: float
    sequence: Optional[int]


def parse_timestamp_from_filename(path: Path) -> float:
    """
    예:
    1778825591815_android_device_003_camera_03_1.jpg
    -> 1778825591815
    """
    m = re.search(r"(\d{10,})", path.name)
    if not m:
        raise ValueError(f"파일명에서 timestamp를 찾을 수 없음: {path.name}")

    return float(m.group(1))


def parse_sequence_from_filename(path: Path) -> Optional[int]:
    """
    예:
    1778825591815_android_device_003_camera_03_1.jpg
    -> 1
    """
    m = re.search(r"_(\d+)\.[^.]+$", path.name)
    if not m:
        return None

    try:
        return int(m.group(1))
    except Exception:
        return None


def find_metadata_path(img_path: Path) -> Optional[Path]:
    """
    이미지에 대응하는 metadata json 찾기.

    주로:
    image.jpg.metadata.json
    """
    candidates = [
        Path(str(img_path) + ".metadata.json"),
        img_path.with_suffix(".metadata.json"),
        img_path.with_suffix(img_path.suffix + ".json"),
    ]

    for c in candidates:
        if c.exists():
            return c

    return None


def read_sequence_from_metadata(meta_path: Optional[Path]) -> Optional[int]:
    if meta_path is None or not meta_path.exists():
        return None

    try:
        with meta_path.open("r", encoding="utf-8") as f:
            data = json.load(f)

        meta = data.get("metadata", data)
        seq = meta.get("frame_sequence", None)

        if seq is None:
            return None

        return int(seq)

    except Exception:
        return None


def load_frames(camera: str, img_dir: Path) -> List[Frame]:
    if not img_dir.exists():
        raise FileNotFoundError(f"폴더가 없음: {img_dir}")

    frames: List[Frame] = []

    for p in sorted(img_dir.iterdir()):
        if not p.is_file():
            continue

        if p.suffix.lower() not in IMG_EXTS:
            continue

        timestamp_ms = parse_timestamp_from_filename(p)
        meta_path = find_metadata_path(p)

        sequence = read_sequence_from_metadata(meta_path)
        if sequence is None:
            sequence = parse_sequence_from_filename(p)

        frames.append(
            Frame(
                camera=camera,
                path=p,
                meta_path=meta_path,
                timestamp_ms=timestamp_ms,
                sequence=sequence,
            )
        )

    frames.sort(key=lambda f: f.timestamp_ms)

    if len(frames) == 0:
        raise RuntimeError(f"이미지 파일을 찾지 못함: {img_dir}")

    return frames


def crop_common_time_range(
    frames1: List[Frame],
    frames2: List[Frame],
    frames3: List[Frame],
) -> Tuple[List[Frame], List[Frame], List[Frame], float, float]:
    """
    세 카메라가 공통으로 존재하는 timestamp 구간만 남긴다.

    시작은 가장 늦게 시작한 카메라 기준.
    끝은 가장 빨리 끝난 카메라 기준.
    """

    common_start = max(
        frames1[0].timestamp_ms,
        frames2[0].timestamp_ms,
        frames3[0].timestamp_ms,
    )

    common_end = min(
        frames1[-1].timestamp_ms,
        frames2[-1].timestamp_ms,
        frames3[-1].timestamp_ms,
    )

    if common_start > common_end:
        raise RuntimeError(
            "세 카메라의 timestamp 공통 구간이 없음.\n"
            f"camera1 range: {frames1[0].timestamp_ms:.0f} ~ {frames1[-1].timestamp_ms:.0f}\n"
            f"camera2 range: {frames2[0].timestamp_ms:.0f} ~ {frames2[-1].timestamp_ms:.0f}\n"
            f"camera3 range: {frames3[0].timestamp_ms:.0f} ~ {frames3[-1].timestamp_ms:.0f}"
        )

    frames1_crop = [
        f for f in frames1
        if common_start <= f.timestamp_ms <= common_end
    ]

    frames2_crop = [
        f for f in frames2
        if common_start <= f.timestamp_ms <= common_end
    ]

    frames3_crop = [
        f for f in frames3
        if common_start <= f.timestamp_ms <= common_end
    ]

    return frames1_crop, frames2_crop, frames3_crop, common_start, common_end


def nearest_one_to_one_match(
    ref_frames: List[Frame],
    target_frames: List[Frame],
    max_dt_ms: Optional[float],
) -> List[Optional[Frame]]:
    """
    camera1 ref frame 기준으로 target camera에서 가장 가까운 timestamp를 찾는다.

    조건:
    - target frame 하나는 한 번만 사용
    - 시간 순서가 뒤집히지 않도록 monotonic matching
    - max_dt_ms보다 차이가 크면 None 처리
    """

    matches: List[Optional[Frame]] = []

    if len(target_frames) == 0:
        return [None for _ in ref_frames]

    j = 0
    n = len(target_frames)

    for ref in ref_frames:
        if j >= n:
            matches.append(None)
            continue

        # 현재 j와 다음 j+1 중 ref timestamp에 더 가까운 쪽으로 전진
        while j + 1 < n:
            cur_diff = abs(target_frames[j].timestamp_ms - ref.timestamp_ms)
            next_diff = abs(target_frames[j + 1].timestamp_ms - ref.timestamp_ms)

            if next_diff <= cur_diff:
                j += 1
            else:
                break

        best = target_frames[j]
        dt = abs(best.timestamp_ms - ref.timestamp_ms)

        if max_dt_ms is None or dt <= max_dt_ms:
            matches.append(best)
            j += 1
        else:
            matches.append(None)

            # target이 ref보다 이미 과거인데 threshold 밖이면 다음으로 넘긴다.
            if best.timestamp_ms < ref.timestamp_ms:
                j += 1

    return matches


def make_triplets(
    camera1_frames: List[Frame],
    camera2_matches: List[Optional[Frame]],
    camera3_matches: List[Optional[Frame]],
) -> List[Tuple[Frame, Frame, Frame]]:
    triplets: List[Tuple[Frame, Frame, Frame]] = []

    for c1, c2, c3 in zip(camera1_frames, camera2_matches, camera3_matches):
        if c2 is None:
            continue
        if c3 is None:
            continue

        triplets.append((c1, c2, c3))

    return triplets


def copy_or_link(src: Path, dst: Path, mode: str) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)

    if dst.exists() or dst.is_symlink():
        dst.unlink()

    if mode == "copy":
        shutil.copy2(src, dst)

    elif mode == "hardlink":
        try:
            os.link(src, dst)
        except Exception:
            shutil.copy2(src, dst)

    elif mode == "symlink":
        os.symlink(src.resolve(), dst)

    else:
        raise ValueError(f"지원하지 않는 mode: {mode}")


def save_frame(
    frame: Frame,
    src_camera_dir: Path,
    out_root: Path,
    mode: str,
) -> None:
    """
    출력 구조:
    out_root/
      camera1/camera1_10fps/
      camera2/camera2_10fps/
      camera3/camera3_10fps/
    """

    dst_img = out_root / frame.camera / src_camera_dir.name / frame.path.name
    copy_or_link(frame.path, dst_img, mode)

    if frame.meta_path is not None and frame.meta_path.exists():
        dst_meta = dst_img.parent / frame.meta_path.name
        copy_or_link(frame.meta_path, dst_meta, mode)


def write_matched_csv(
    csv_path: Path,
    triplets: List[Tuple[Frame, Frame, Frame]],
) -> None:
    csv_path.parent.mkdir(parents=True, exist_ok=True)

    with csv_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)

        writer.writerow([
            "sync_idx",

            "camera1_file",
            "camera1_sequence",
            "camera1_timestamp_ms",

            "camera2_file",
            "camera2_sequence",
            "camera2_timestamp_ms",
            "camera2_dt_to_camera1_ms",

            "camera3_file",
            "camera3_sequence",
            "camera3_timestamp_ms",
            "camera3_dt_to_camera1_ms",
        ])

        for i, (c1, c2, c3) in enumerate(triplets, start=1):
            writer.writerow([
                i,

                c1.path.name,
                c1.sequence,
                f"{c1.timestamp_ms:.0f}",

                c2.path.name,
                c2.sequence,
                f"{c2.timestamp_ms:.0f}",
                f"{c2.timestamp_ms - c1.timestamp_ms:.3f}",

                c3.path.name,
                c3.sequence,
                f"{c3.timestamp_ms:.0f}",
                f"{c3.timestamp_ms - c1.timestamp_ms:.3f}",
            ])


def write_summary(
    summary_path: Path,
    input_counts: Tuple[int, int, int],
    cropped_counts: Tuple[int, int, int],
    triplets: List[Tuple[Frame, Frame, Frame]],
    common_start: float,
    common_end: float,
    max_dt_ms: Optional[float],
) -> None:
    dt2 = [abs(c2.timestamp_ms - c1.timestamp_ms) for c1, c2, _ in triplets]
    dt3 = [abs(c3.timestamp_ms - c1.timestamp_ms) for c1, _, c3 in triplets]

    def stats(values: List[float]) -> str:
        if len(values) == 0:
            return "N/A"

        return (
            f"mean={statistics.mean(values):.3f} ms, "
            f"median={statistics.median(values):.3f} ms, "
            f"max={max(values):.3f} ms"
        )

    with summary_path.open("w", encoding="utf-8") as f:
        f.write("Camera1 reference nearest timestamp matching summary\n")
        f.write("====================================================\n\n")

        f.write("Timestamp source: filename prefix, millisecond unit\n")
        f.write(f"max_dt_ms: {max_dt_ms}\n\n")

        f.write("[Input frame count]\n")
        f.write(f"camera1: {input_counts[0]}\n")
        f.write(f"camera2: {input_counts[1]}\n")
        f.write(f"camera3: {input_counts[2]}\n\n")

        f.write("[Common time range]\n")
        f.write(f"common_start_ms: {common_start:.0f}\n")
        f.write(f"common_end_ms:   {common_end:.0f}\n")
        f.write(f"duration_sec:     {(common_end - common_start) / 1000.0:.3f}\n\n")

        f.write("[Frame count after common range crop]\n")
        f.write(f"camera1: {cropped_counts[0]}\n")
        f.write(f"camera2: {cropped_counts[1]}\n")
        f.write(f"camera3: {cropped_counts[2]}\n\n")

        f.write("[Matched triplet count]\n")
        f.write(f"triplets: {len(triplets)}\n")
        f.write(f"output camera1: {len(triplets)}\n")
        f.write(f"output camera2: {len(triplets)}\n")
        f.write(f"output camera3: {len(triplets)}\n\n")

        f.write("[Timestamp difference after matching]\n")
        f.write(f"abs(camera2 - camera1): {stats(dt2)}\n")
        f.write(f"abs(camera3 - camera1): {stats(dt3)}\n")


def parse_args():
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--root",
        type=str,
        default="/home/curica/capstone_real/rotated_10fps",
        help="rotated_10fps root folder",
    )

    parser.add_argument(
        "--out_dir",
        type=str,
        default="/home/curica/capstone_real/synced_10fps_camera1_ref",
        help="matched output folder",
    )

    parser.add_argument(
        "--max-dt-ms",
        type=float,
        default=80.0,
        help=(
            "nearest matching 허용 최대 시간차(ms). "
            "10fps면 1 frame이 약 100ms이므로 50~120ms 정도 권장. "
            "음수로 주면 threshold 없이 가장 가까운 프레임을 사용."
        ),
    )

    parser.add_argument(
        "--mode",
        choices=["copy", "hardlink", "symlink"],
        default="copy",
        help="output 저장 방식",
    )

    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="실제 복사 없이 매칭 결과만 확인",
    )

    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="output 폴더가 있으면 삭제 후 다시 생성",
    )

    return parser.parse_args()


def main():
    args = parse_args()

    root = Path(args.root)
    out_dir = Path(args.out_dir)

    camera1_dir = root / "camera1" / "camera1_10fps"
    camera2_dir = root / "camera2" / "camera2_10fps"
    camera3_dir = root / "camera3" / "camera3_10fps"

    max_dt_ms = None if args.max_dt_ms < 0 else args.max_dt_ms

    print("[INFO] Input folders")
    print(f"  camera1: {camera1_dir}")
    print(f"  camera2: {camera2_dir}")
    print(f"  camera3: {camera3_dir}")
    print()

    print("[INFO] Loading frames...")
    frames1 = load_frames("camera1", camera1_dir)
    frames2 = load_frames("camera2", camera2_dir)
    frames3 = load_frames("camera3", camera3_dir)

    input_counts = (len(frames1), len(frames2), len(frames3))

    print("[INFO] Original timestamp range")
    print(f"  camera1: {frames1[0].timestamp_ms:.0f} ~ {frames1[-1].timestamp_ms:.0f}, count={len(frames1)}")
    print(f"  camera2: {frames2[0].timestamp_ms:.0f} ~ {frames2[-1].timestamp_ms:.0f}, count={len(frames2)}")
    print(f"  camera3: {frames3[0].timestamp_ms:.0f} ~ {frames3[-1].timestamp_ms:.0f}, count={len(frames3)}")
    print()

    frames1, frames2, frames3, common_start, common_end = crop_common_time_range(
        frames1,
        frames2,
        frames3,
    )

    cropped_counts = (len(frames1), len(frames2), len(frames3))

    print("[INFO] Common time range")
    print(f"  common_start_ms: {common_start:.0f}")
    print(f"  common_end_ms:   {common_end:.0f}")
    print(f"  duration_sec:     {(common_end - common_start) / 1000.0:.3f}")
    print()

    print("[INFO] Frame count after common range crop")
    print(f"  camera1: {len(frames1)}")
    print(f"  camera2: {len(frames2)}")
    print(f"  camera3: {len(frames3)}")
    print()

    if len(frames1) == 0 or len(frames2) == 0 or len(frames3) == 0:
        raise RuntimeError("공통 시간 구간 crop 이후 남은 프레임이 0개인 카메라가 있음.")

    print("[INFO] Matching camera2/camera3 to camera1...")
    match2 = nearest_one_to_one_match(frames1, frames2, max_dt_ms)
    match3 = nearest_one_to_one_match(frames1, frames3, max_dt_ms)

    triplets = make_triplets(frames1, match2, match3)

    print("[INFO] Matching result")
    print(f"  matched triplets: {len(triplets)}")
    print(f"  output camera1:   {len(triplets)}")
    print(f"  output camera2:   {len(triplets)}")
    print(f"  output camera3:   {len(triplets)}")
    print()

    if len(triplets) == 0:
        print("[ERROR] 매칭된 triplet이 0개임.")
        print("        --max-dt-ms 값을 키워서 다시 확인해라.")
        print("        예: --max-dt-ms 150")
        return

    dt2 = [abs(c2.timestamp_ms - c1.timestamp_ms) for c1, c2, _ in triplets]
    dt3 = [abs(c3.timestamp_ms - c1.timestamp_ms) for c1, _, c3 in triplets]

    print("[INFO] Timestamp diff after matching")
    print(f"  camera2-camera1: median={statistics.median(dt2):.3f} ms, max={max(dt2):.3f} ms")
    print(f"  camera3-camera1: median={statistics.median(dt3):.3f} ms, max={max(dt3):.3f} ms")
    print()

    if args.dry_run:
        print("[DRY-RUN] 실제 파일 복사는 하지 않음.")
        return

    if out_dir.exists():
        if args.overwrite:
            shutil.rmtree(out_dir)
        else:
            raise FileExistsError(
                f"output 폴더가 이미 존재함: {out_dir}\n"
                f"덮어쓰려면 --overwrite 옵션을 붙여라."
            )

    out_dir.mkdir(parents=True, exist_ok=True)

    print("[INFO] Saving matched frames...")

    for idx, (c1, c2, c3) in enumerate(triplets, start=1):
        save_frame(c1, camera1_dir, out_dir, args.mode)
        save_frame(c2, camera2_dir, out_dir, args.mode)
        save_frame(c3, camera3_dir, out_dir, args.mode)

        if idx % 500 == 0:
            print(f"  copied {idx}/{len(triplets)} triplets")

    csv_path = out_dir / "matched_triplets.csv"
    summary_path = out_dir / "summary.txt"

    write_matched_csv(csv_path, triplets)

    write_summary(
        summary_path=summary_path,
        input_counts=input_counts,
        cropped_counts=cropped_counts,
        triplets=triplets,
        common_start=common_start,
        common_end=common_end,
        max_dt_ms=max_dt_ms,
    )

    print()
    print("[DONE] Saved matched dataset")
    print(f"  output:  {out_dir}")
    print(f"  csv:     {csv_path}")
    print(f"  summary: {summary_path}")


if __name__ == "__main__":
    main()