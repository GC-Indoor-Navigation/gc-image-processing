#!/usr/bin/env bash
set -euo pipefail

image="gc-mmpose-processing:smoke"
http_port="9000"
grpc_port="50051"
pose2d="human"
device="cuda:0"
kpt_thr="0.30"
max_reproj_error="40.0"
images_undistorted="false"
extrinsic_source="auto"
extrinsic_convention="world_to_camera"
temp_dir=""
preload="true"
result_storage="true"
result_storage_dir="runtime/outputs/mmpose"
relay_run_idle_reset_sec="5.0"
alerts_enabled="false"
alerts_target_url=""
alerts_timeout_sec="1.0"
alerts_ttl_ms="500"
cpu="false"
no_gpu="false"
calib_json=""
camera_mappings=()

usage() {
  cat <<'EOF'
Usage:
  scripts/mmpose/run_server_docker.sh \
    --calib-json runtime/calibration/active_calibration.json \
    --camera-mapping android_device_001=Camera1 \
    --camera-mapping android_device_002=Camera2 \
    --camera-mapping android_device_003=Camera3

Options:
  --image IMAGE                         Docker image (default: gc-mmpose-processing:smoke)
  --calib-json PATH                     Calibration JSON inside this repository (required)
  --camera-mapping DEVICE=CAMERA        Stream device ID to calibration camera name (repeatable, required)
  --http-port PORT                      Host HTTP port (default: 9000)
  --grpc-port PORT                      Host gRPC port (default: 50051)
  --pose2d NAME                         MMPose pose2d preset (default: human)
  --device DEVICE                       MMPose device (default: cuda:0)
  --kpt-thr VALUE                       Keypoint threshold (default: 0.30)
  --max-reproj-error VALUE              Max reprojection error (default: 40.0)
  --images-undistorted                  Treat incoming images as already undistorted
  --extrinsic-source VALUE              Calibration extrinsic source (default: auto)
  --extrinsic-convention VALUE          Extrinsic convention (default: world_to_camera)
  --temp-dir PATH                       Temp dir inside this repository
  --no-preload                          Disable startup MMPose preload
  --no-result-storage                   Disable JSONL result storage
  --result-storage-dir PATH             Result storage dir (default: runtime/outputs/mmpose)
  --relay-run-idle-reset-sec VALUE      Idle seconds before new relay run detection (default: 5.0)
  --alerts-enabled                      Publish alert events to Stream Server
  --alerts-target-url URL               Stream Server alert endpoint URL
  --alerts-timeout-sec VALUE            Alert publish timeout seconds (default: 1.0)
  --alerts-ttl-ms VALUE                 Alert event TTL milliseconds (default: 500)
  --cpu                                 Run MMPose on CPU and do not pass --gpus all
  --no-gpu                              Do not pass --gpus all but keep selected --device
  -h, --help                            Show this help
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --image) image="$2"; shift 2 ;;
    --calib-json) calib_json="$2"; shift 2 ;;
    --camera-mapping) camera_mappings+=("$2"); shift 2 ;;
    --http-port) http_port="$2"; shift 2 ;;
    --grpc-port) grpc_port="$2"; shift 2 ;;
    --pose2d) pose2d="$2"; shift 2 ;;
    --device) device="$2"; shift 2 ;;
    --kpt-thr) kpt_thr="$2"; shift 2 ;;
    --max-reproj-error) max_reproj_error="$2"; shift 2 ;;
    --images-undistorted) images_undistorted="true"; shift ;;
    --extrinsic-source) extrinsic_source="$2"; shift 2 ;;
    --extrinsic-convention) extrinsic_convention="$2"; shift 2 ;;
    --temp-dir) temp_dir="$2"; shift 2 ;;
    --no-preload) preload="false"; shift ;;
    --no-result-storage) result_storage="false"; shift ;;
    --result-storage-dir) result_storage_dir="$2"; shift 2 ;;
    --relay-run-idle-reset-sec) relay_run_idle_reset_sec="$2"; shift 2 ;;
    --alerts-enabled) alerts_enabled="true"; shift ;;
    --alerts-target-url) alerts_target_url="$2"; shift 2 ;;
    --alerts-timeout-sec) alerts_timeout_sec="$2"; shift 2 ;;
    --alerts-ttl-ms) alerts_ttl_ms="$2"; shift 2 ;;
    --cpu) cpu="true"; shift ;;
    --no-gpu) no_gpu="true"; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
done

if [[ -z "$calib_json" ]]; then
  echo "--calib-json is required" >&2
  usage >&2
  exit 2
fi

if [[ ${#camera_mappings[@]} -eq 0 ]]; then
  echo "At least one --camera-mapping is required" >&2
  usage >&2
  exit 2
fi

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "$script_dir/../.." && pwd)"

to_container_path() {
  local input_path="$1"
  local resolved
  resolved="$(realpath "$input_path")"
  case "$resolved" in
    "$repo_root"|"$repo_root"/*) ;;
    *)
      echo "Path must be inside repository because only the repository is mounted: $resolved" >&2
      exit 2
      ;;
  esac
  local relative="${resolved#"$repo_root"}"
  relative="${relative#/}"
  echo "/workspace/gc-image-processing/$relative"
}

container_calib="$(to_container_path "$calib_json")"
effective_device="$device"
if [[ "$cpu" == "true" ]]; then
  effective_device="cpu"
fi

camera_mapping_csv="$(IFS=,; echo "${camera_mappings[*]}")"
container_result_storage_dir="/workspace/gc-image-processing/${result_storage_dir//\\//}"

docker_args=(
  run
  --rm
  -p "$http_port:9000"
  -p "$grpc_port:50051"
  -v "$repo_root:/workspace/gc-image-processing"
  -w /workspace/gc-image-processing
  -e PROCESSING_HTTP_HOST=0.0.0.0
  -e PROCESSING_HTTP_PORT=9000
  -e PROCESSING_GRPC_BIND=0.0.0.0:50051
  -e PROCESSING_RELAY_RUN_IDLE_RESET_SEC="$relay_run_idle_reset_sec"
  -e PROCESSING_RESULT_STORAGE_ENABLED="$result_storage"
  -e PROCESSING_RESULT_STORAGE_DIR="$container_result_storage_dir"
  -e PROCESSING_PROCESSOR=mmpose_triangulation
  -e PROCESSING_MMPOSE_CALIB_JSON="$container_calib"
  -e PROCESSING_MMPOSE_CAMERA_MAPPING="$camera_mapping_csv"
  -e PROCESSING_MMPOSE_POSE2D="$pose2d"
  -e PROCESSING_MMPOSE_DEVICE="$effective_device"
  -e PROCESSING_MMPOSE_KPT_THR="$kpt_thr"
  -e PROCESSING_MMPOSE_MAX_REPROJ_ERROR="$max_reproj_error"
  -e PROCESSING_MMPOSE_IMAGES_UNDISTORTED="$images_undistorted"
  -e PROCESSING_MMPOSE_EXTRINSIC_SOURCE="$extrinsic_source"
  -e PROCESSING_MMPOSE_EXTRINSIC_CONVENTION="$extrinsic_convention"
  -e PROCESSING_MMPOSE_PRELOAD="$preload"
  -e PROCESSING_ALERTS_ENABLED="$alerts_enabled"
  -e PROCESSING_ALERTS_TARGET_URL="$alerts_target_url"
  -e PROCESSING_ALERTS_TIMEOUT_SEC="$alerts_timeout_sec"
  -e PROCESSING_ALERTS_TTL_MS="$alerts_ttl_ms"
)

if [[ -n "$temp_dir" ]]; then
  docker_args+=(-e "PROCESSING_MMPOSE_TEMP_DIR=/workspace/gc-image-processing/${temp_dir//\\//}")
fi

if [[ "$no_gpu" != "true" && "$cpu" != "true" ]]; then
  docker_args+=(--gpus all)
fi

docker_args+=(
  "$image"
  --host 0.0.0.0
  --port 9000
  --grpc-bind 0.0.0.0:50051
  --relay-run-idle-reset-sec "$relay_run_idle_reset_sec"
  --processor mmpose_triangulation
  --mmpose-calib-json "$container_calib"
  --mmpose-pose2d "$pose2d"
  --mmpose-device "$effective_device"
  --mmpose-kpt-thr "$kpt_thr"
  --mmpose-max-reproj-error "$max_reproj_error"
  --mmpose-extrinsic-source "$extrinsic_source"
  --mmpose-extrinsic-convention "$extrinsic_convention"
  --alerts-target-url "$alerts_target_url"
  --alerts-timeout-sec "$alerts_timeout_sec"
  --alerts-ttl-ms "$alerts_ttl_ms"
)

for mapping in "${camera_mappings[@]}"; do
  docker_args+=(--mmpose-camera-mapping "$mapping")
done

if [[ "$images_undistorted" == "true" ]]; then
  docker_args+=(--mmpose-images-undistorted)
fi

if [[ "$preload" == "true" ]]; then
  docker_args+=(--mmpose-preload)
fi

if [[ "$alerts_enabled" == "true" ]]; then
  docker_args+=(--alerts-enabled)
fi

if [[ "$result_storage" == "true" ]]; then
  docker_args+=(--result-storage-enabled --result-storage-dir "$container_result_storage_dir")
fi

if [[ -n "$temp_dir" ]]; then
  docker_args+=(--mmpose-temp-dir "/workspace/gc-image-processing/${temp_dir//\\//}")
fi

echo "Running MMPose processing server"
echo "Image:   $image"
echo "HTTP:    http://localhost:$http_port"
echo "gRPC:    localhost:$grpc_port"
echo "Calib:   $container_calib"
echo "Device:  $effective_device"
echo "Mapping: $camera_mapping_csv"
echo "Preload: $preload"
echo "Results: $container_result_storage_dir"
echo "Alerts: $alerts_enabled"
echo "AlertTarget: $alerts_target_url"

exec docker "${docker_args[@]}"
