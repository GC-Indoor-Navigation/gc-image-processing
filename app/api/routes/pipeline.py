from dataclasses import asdict, is_dataclass
from typing import Any

from fastapi import APIRouter, Depends
from fastapi import Query
from fastapi.responses import HTMLResponse

from app.api.deps import (
    get_motion_capture_worker,
    get_processing_queue,
    get_processing_service,
    get_result_store,
    get_settings,
    get_sync_matcher,
)
from app.core.config import Settings
from app.pipeline.queue import ProcessingQueue
from app.pipeline.result_store import JsonlTriangulationResultStore
from app.pipeline.worker import MotionCaptureWorker
from app.schemas.pipeline import (
    FrameSetResponse,
    LatestTriangulationResultResponse,
    PipelineStatusResponse,
    ResultHistoryItemResponse,
    ResultSummaryResponse,
    ResultStorageStatusResponse,
)
from app.services.processing import ProcessingService
from app.sync.matcher import SyncMatcher


router = APIRouter(tags=["pipeline"])


def _empty_sync_status():
    return {
        "matched_count": 0,
        "missed_count": 0,
        "duplicate_count": 0,
        "ignored_count": 0,
        "last_frame_set_id": None,
        "last_anchor_timestamp_ms": None,
        "last_max_delta_ms": None,
        "last_missing_cameras": [],
        "last_reason": None,
    }


@router.get("/pipeline/status", response_model=PipelineStatusResponse)
def pipeline_status(
    settings: Settings = Depends(get_settings),
    service: ProcessingService = Depends(get_processing_service),
    processing_queue: ProcessingQueue | None = Depends(get_processing_queue),
    worker: MotionCaptureWorker | None = Depends(get_motion_capture_worker),
    sync_matcher: SyncMatcher | None = Depends(get_sync_matcher),
):
    sync_status = (
        sync_matcher.status()
        if sync_matcher is not None
        else _empty_sync_status()
    )
    queue_status = (
        processing_queue.status()
        if processing_queue is not None
        else {"queue_size": 0, "enqueued_count": 0, "dequeued_count": 0}
    )
    worker_status = (
        worker.status()
        if worker is not None
        else {
            "running": False,
            "processed_count": 0,
            "error_count": 0,
            "last_processed_frame_set_id": None,
            "last_processed_at": None,
            "last_result": None,
            "last_error": None,
        }
    )
    return {
        "relay_path": {
            "primary_method": "StreamFrameSets",
            "raw_stream_frames_mode": "legacy_fallback",
            "raw_sync_enabled": settings.sync_enabled,
        },
        "sync": {
            "enabled": settings.sync_enabled,
            "expected_cameras": list(settings.expected_cameras),
            "window_ms": settings.sync_window_ms,
            **sync_status,
        },
        "relay_frame_sets": service.relay_frame_set_status(),
        "queue": queue_status,
        "worker": {
            "enabled": settings.worker_enabled,
            **worker_status,
        },
    }


@router.get(
    "/pipeline/results/storage",
    response_model=ResultStorageStatusResponse,
)
def result_storage_status(
    result_store: JsonlTriangulationResultStore | None = Depends(get_result_store),
):
    if result_store is None:
        return {
            "enabled": False,
            "output_dir": None,
            "last_written_path": None,
            "runs": {},
        }
    return result_store.status()


@router.get(
    "/pipeline/results/history",
    response_model=list[ResultHistoryItemResponse],
)
def result_history(
    limit: int = Query(20, ge=1, le=500),
    result_store: JsonlTriangulationResultStore | None = Depends(get_result_store),
):
    if result_store is None:
        return []
    return result_store.read_history(limit=limit)


@router.get(
    "/pipeline/results/summary",
    response_model=ResultSummaryResponse,
)
def result_summary(
    result_store: JsonlTriangulationResultStore | None = Depends(get_result_store),
):
    if result_store is None:
        return {
            "enabled": False,
            "output_dir": None,
            "run_count": 0,
            "runs": [],
        }
    return result_store.summarize()


@router.get(
    "/pipeline/results/viewer",
    response_class=HTMLResponse,
)
def live_skeleton_viewer():
    return HTMLResponse(LIVE_SKELETON_VIEWER_HTML)


@router.get(
    "/pipeline/results/latest",
    response_model=LatestTriangulationResultResponse,
)
def latest_triangulation_result(
    worker: MotionCaptureWorker | None = Depends(get_motion_capture_worker),
):
    if worker is None:
        return {
            "available": False,
            "processor": None,
            "processing_result": None,
            "result": None,
            "last_error": None,
        }

    processor = worker.processor
    skeleton_result = getattr(processor, "last_skeleton_result", None)
    result = _serialize_result(skeleton_result)
    return {
        "available": result is not None,
        "processor": processor.__class__.__name__,
        "processing_result": (
            asdict(worker.last_result)
            if worker.last_result is not None
            else None
        ),
        "result": result,
        "last_error": worker.last_error,
    }


@router.get("/pipeline/recent-frame-sets", response_model=list[FrameSetResponse])
def recent_frame_sets(
    sync_matcher: SyncMatcher | None = Depends(get_sync_matcher),
):
    if sync_matcher is None:
        return []
    return [
        {
            "frame_set_id": frame_set.frame_set_id,
            "anchor_timestamp_ms": frame_set.anchor_timestamp_ms,
            "max_delta_ms": frame_set.max_delta_ms,
            "frames": {
                device_id: {
                    "device_id": frame.device_id,
                    "timestamp_ms": frame.timestamp_ms,
                    "sequence": frame.sequence,
                    "content_type": frame.content_type,
                    "image_size": frame.image_size,
                    "source_file_path": frame.source_file_path,
                    "source_frame_id": frame.source_frame_id,
                }
                for device_id, frame in frame_set.frames.items()
            },
        }
        for frame_set in sync_matcher.recent_frame_sets()
    ]


def _serialize_result(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, dict):
        return value
    return None


LIVE_SKELETON_VIEWER_HTML = r"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Live 3D Skeleton</title>
  <style>
    :root {
      --bg: #f7f8fb;
      --panel: #ffffff;
      --text: #111827;
      --muted: #6b7280;
      --line: #d7dce5;
      --blue: #2563eb;
      --red: #dc2626;
      --green: #059669;
      --warn: #d97706;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      font-family: Inter, Segoe UI, Arial, sans-serif;
      background: var(--bg);
      color: var(--text);
    }
    main {
      min-height: 100vh;
      display: grid;
      grid-template-columns: minmax(0, 1fr) 320px;
    }
    .stage {
      min-height: 100vh;
      display: grid;
      grid-template-rows: auto minmax(0, 1fr) auto;
      padding: 20px;
    }
    h1 {
      margin: 0;
      font-size: 20px;
      font-weight: 700;
      letter-spacing: 0;
    }
    .subtitle {
      margin-top: 4px;
      color: var(--muted);
      font-size: 12px;
    }
    canvas {
      width: 100%;
      height: 100%;
      min-height: 520px;
      display: block;
      background: #ffffff;
      border: 1px solid var(--line);
      border-radius: 8px;
      margin-top: 16px;
    }
    .timeline {
      display: grid;
      grid-template-columns: auto auto minmax(0, 1fr) auto auto;
      gap: 12px;
      align-items: center;
      margin-top: 14px;
    }
    aside {
      min-height: 100vh;
      background: var(--panel);
      border-left: 1px solid var(--line);
      padding: 20px;
      overflow: auto;
    }
    .section {
      padding: 16px 0;
      border-bottom: 1px solid var(--line);
    }
    .section:first-child { padding-top: 0; }
    .label {
      display: block;
      margin-bottom: 8px;
      font-size: 12px;
      font-weight: 700;
      color: #374151;
    }
    input[type="range"] { width: 100%; }
    button {
      border: 1px solid var(--line);
      background: #ffffff;
      color: var(--text);
      border-radius: 6px;
      padding: 8px 10px;
      font-weight: 700;
      cursor: pointer;
    }
    button:hover { border-color: var(--blue); }
    .pill {
      display: inline-flex;
      align-items: center;
      gap: 6px;
      border: 1px solid var(--line);
      border-radius: 999px;
      padding: 6px 10px;
      font-size: 12px;
      color: var(--muted);
      background: #fff;
    }
    .dot {
      width: 8px;
      height: 8px;
      border-radius: 50%;
      background: var(--warn);
    }
    .dot.live { background: var(--green); }
    .kv {
      display: grid;
      grid-template-columns: 1fr auto;
      gap: 8px;
      font-size: 13px;
      line-height: 1.6;
    }
    .kv span:nth-child(odd) { color: var(--muted); }
    .frames {
      font-size: 12px;
      color: var(--muted);
      overflow-wrap: anywhere;
      line-height: 1.5;
    }
    @media (max-width: 900px) {
      main { grid-template-columns: 1fr; }
      aside { min-height: auto; border-left: 0; border-top: 1px solid var(--line); }
      .stage { min-height: auto; }
      canvas { min-height: 420px; }
      .timeline { grid-template-columns: auto auto minmax(0, 1fr); }
    }
  </style>
</head>
<body>
<main>
  <section class="stage">
    <header>
      <h1>Live 3D Skeleton</h1>
      <div class="subtitle">Polling /pipeline/results/latest</div>
    </header>
    <canvas id="canvas"></canvas>
    <div class="timeline">
      <span class="pill"><span id="liveDot" class="dot"></span><span id="liveText">Waiting</span></span>
      <button id="follow">Follow Latest</button>
      <input id="frame" type="range" min="0" value="0">
      <span id="frameText"></span>
      <button id="reset">Reset View</button>
    </div>
  </section>
  <aside>
    <div class="section">
      <label class="label" for="yaw">Yaw</label>
      <input id="yaw" type="range" min="-180" max="180" value="-35">
      <label class="label" for="pitch" style="margin-top:14px">Pitch</label>
      <input id="pitch" type="range" min="-90" max="90" value="-18">
      <label class="label" for="zoom" style="margin-top:14px">Zoom</label>
      <input id="zoom" type="range" min="80" max="360" value="210">
    </div>
    <div class="section">
      <div class="kv" id="stats"></div>
    </div>
    <div class="section">
      <div class="label">Source Frames</div>
      <div class="frames" id="frames"></div>
    </div>
  </aside>
</main>
<script>
const edges = [
  ["left_eye", "right_eye"], ["left_eye", "left_ear"], ["right_eye", "right_ear"],
  ["left_shoulder", "right_shoulder"], ["left_shoulder", "left_elbow"],
  ["left_elbow", "left_wrist"], ["right_shoulder", "right_elbow"],
  ["right_elbow", "right_wrist"], ["left_shoulder", "left_hip"],
  ["right_shoulder", "right_hip"], ["left_hip", "right_hip"],
  ["left_hip", "left_knee"], ["left_knee", "left_ankle"],
  ["right_hip", "right_knee"], ["right_knee", "right_ankle"]
];
const canvas = document.getElementById("canvas");
const ctx = canvas.getContext("2d");
const frameInput = document.getElementById("frame");
const frameText = document.getElementById("frameText");
const yawInput = document.getElementById("yaw");
const pitchInput = document.getElementById("pitch");
const zoomInput = document.getElementById("zoom");
const followButton = document.getElementById("follow");
const resetButton = document.getElementById("reset");
const liveDot = document.getElementById("liveDot");
const liveText = document.getElementById("liveText");
const stats = document.getElementById("stats");
const sourceFrames = document.getElementById("frames");
let frames = [];
let seenKeys = new Set();
let followLatest = true;

function resizeCanvas() {
  const rect = canvas.getBoundingClientRect();
  const ratio = window.devicePixelRatio || 1;
  canvas.width = Math.max(1, Math.floor(rect.width * ratio));
  canvas.height = Math.max(1, Math.floor(rect.height * ratio));
  ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
}

function normalizeLatest(payload) {
  if (!payload || !payload.available || !payload.result) return null;
  const result = payload.result;
  const processing = payload.processing_result || {};
  const joints = {};
  const scores = {};
  const reproj = {};
  for (const [name, value] of Object.entries(result.joints_world || {})) {
    if (Array.isArray(value)) {
      joints[name] = value;
      continue;
    }
    if (value && Array.isArray(value.xyz)) {
      joints[name] = value.xyz;
      scores[name] = value.score;
      reproj[name] = value.reproj_error_px;
    }
  }
  return {
    key: `${processing.started_at || ""}:${result.frame_set_id}`,
    frame_set_id: result.frame_set_id,
    status: processing.status,
    elapsed_ms: processing.elapsed_ms,
    num_valid_joints: result.num_valid_joints,
    avg_reproj_error_px: result.avg_reproj_error_px,
    max_delta_ms: result.max_delta_ms,
    joints_world: joints,
    joint_scores: scores,
    joint_reproj_error_px: reproj,
    source_frames: result.source_frames || {}
  };
}

async function pollLatest() {
  try {
    const response = await fetch("/pipeline/results/latest", { cache: "no-store" });
    const latest = normalizeLatest(await response.json());
    if (latest && Object.keys(latest.joints_world).length > 0 && !seenKeys.has(latest.key)) {
      seenKeys.add(latest.key);
      frames.push(latest);
      frameInput.max = Math.max(frames.length - 1, 0);
      if (followLatest) frameInput.value = String(frames.length - 1);
      liveDot.classList.add("live");
      liveText.textContent = "Live";
      draw();
    } else if (!latest) {
      liveDot.classList.remove("live");
      liveText.textContent = "Waiting";
      draw();
    }
  } catch (error) {
    liveDot.classList.remove("live");
    liveText.textContent = "Disconnected";
  }
}

function rotate(point, yawDeg, pitchDeg) {
  const yaw = yawDeg * Math.PI / 180;
  const pitch = pitchDeg * Math.PI / 180;
  const cy = Math.cos(yaw), sy = Math.sin(yaw);
  const cp = Math.cos(pitch), sp = Math.sin(pitch);
  const x1 = point[0] * cy - point[2] * sy;
  const z1 = point[0] * sy + point[2] * cy;
  const y2 = point[1] * cp - z1 * sp;
  const z2 = point[1] * sp + z1 * cp;
  return [x1, y2, z2];
}

function project(point, center, scale, yaw, pitch) {
  const p = rotate([point[0] - center[0], point[1] - center[1], point[2] - center[2]], yaw, pitch);
  return [canvas.clientWidth / 2 + p[0] * scale, canvas.clientHeight / 2 - p[1] * scale, p[2]];
}

function centerOf(joints) {
  const points = Object.values(joints).filter(v => Array.isArray(v) && v.length === 3);
  const center = [0, 0, 0];
  for (const p of points) {
    center[0] += p[0]; center[1] += p[1]; center[2] += p[2];
  }
  center[0] /= points.length || 1;
  center[1] /= points.length || 1;
  center[2] /= points.length || 1;
  return center;
}

function draw() {
  resizeCanvas();
  ctx.clearRect(0, 0, canvas.clientWidth, canvas.clientHeight);
  const frame = frames[Number(frameInput.value)];
  if (!frame) {
    ctx.fillStyle = "#6b7280";
    ctx.font = "14px Segoe UI, Arial";
    ctx.fillText("Waiting for processed skeleton results...", 24, 36);
    frameText.textContent = `0 / 0`;
    stats.innerHTML = "";
    sourceFrames.innerHTML = "";
    return;
  }
  const joints = frame.joints_world || {};
  const center = centerOf(joints);
  const yaw = Number(yawInput.value);
  const pitch = Number(pitchInput.value);
  const scale = Number(zoomInput.value);
  const projected = {};
  for (const [name, point] of Object.entries(joints)) {
    projected[name] = project(point, center, scale, yaw, pitch);
  }
  drawGrid(center, scale, yaw, pitch);
  for (const edge of edges) {
    const a = projected[edge[0]], b = projected[edge[1]];
    if (!a || !b) continue;
    ctx.strokeStyle = edge[0].includes("right") || edge[1].includes("right") ? "#2563eb" : "#dc2626";
    ctx.lineWidth = 4;
    ctx.lineCap = "round";
    ctx.beginPath();
    ctx.moveTo(a[0], a[1]);
    ctx.lineTo(b[0], b[1]);
    ctx.stroke();
  }
  for (const [name, p] of Object.entries(projected)) {
    const score = frame.joint_scores?.[name] ?? 1;
    ctx.fillStyle = score >= 0.8 ? "#059669" : "#d97706";
    ctx.beginPath();
    ctx.arc(p[0], p[1], 5, 0, Math.PI * 2);
    ctx.fill();
  }
  updateText(frame);
}

function drawGrid(center, scale, yaw, pitch) {
  ctx.strokeStyle = "#e5e7eb";
  ctx.lineWidth = 1;
  for (let i = -2; i <= 2; i++) {
    const a = project([center[0] - 1.0, center[1], center[2] + i * 0.25], center, scale, yaw, pitch);
    const b = project([center[0] + 1.0, center[1], center[2] + i * 0.25], center, scale, yaw, pitch);
    ctx.beginPath(); ctx.moveTo(a[0], a[1]); ctx.lineTo(b[0], b[1]); ctx.stroke();
    const c = project([center[0] + i * 0.25, center[1], center[2] - 1.0], center, scale, yaw, pitch);
    const d = project([center[0] + i * 0.25, center[1], center[2] + 1.0], center, scale, yaw, pitch);
    ctx.beginPath(); ctx.moveTo(c[0], c[1]); ctx.lineTo(d[0], d[1]); ctx.stroke();
  }
}

function updateText(frame) {
  frameText.textContent = `${Number(frameInput.value) + 1} / ${frames.length}`;
  stats.innerHTML = [
    ["Frame set", frame.frame_set_id],
    ["Status", frame.status],
    ["Valid joints", frame.num_valid_joints],
    ["Reproj error", format(frame.avg_reproj_error_px, " px")],
    ["Max delta", format(frame.max_delta_ms, " ms")],
    ["Elapsed", format(frame.elapsed_ms, " ms")]
  ].map(([k, v]) => `<span>${k}</span><strong>${v ?? ""}</strong>`).join("");
  sourceFrames.innerHTML = Object.entries(frame.source_frames || {})
    .map(([camera, data]) => `<strong>${camera}</strong><br>${data.source_file_path || ""}`)
    .join("<br><br>");
}

function format(value, suffix) {
  if (value === null || value === undefined) return "";
  return `${Number(value).toFixed(2)}${suffix}`;
}

frameInput.addEventListener("input", () => { followLatest = false; draw(); });
followButton.addEventListener("click", () => {
  followLatest = true;
  if (frames.length > 0) frameInput.value = String(frames.length - 1);
  draw();
});
resetButton.addEventListener("click", () => {
  yawInput.value = "-35";
  pitchInput.value = "-18";
  zoomInput.value = "210";
  draw();
});
for (const input of [yawInput, pitchInput, zoomInput]) input.addEventListener("input", draw);
window.addEventListener("resize", draw);
draw();
pollLatest();
setInterval(pollLatest, 1000);
</script>
</body>
</html>
"""
