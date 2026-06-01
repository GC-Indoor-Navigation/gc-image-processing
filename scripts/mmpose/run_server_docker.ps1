param(
    [Parameter(Mandatory = $true)]
    [string]$CalibJson,

    [Parameter(Mandatory = $true)]
    [string[]]$CameraMapping,

    [string]$Image = "gc-mmpose-processing:smoke",
    [int]$HttpPort = 9000,
    [int]$GrpcPort = 50051,
    [string]$Pose2d = "human",
    [string]$Device = "cuda:0",
    [double]$KptThr = 0.30,
    [double]$MaxReprojError = 40.0,
    [switch]$ImagesUndistorted,
    [string]$ExtrinsicSource = "auto",
    [string]$ExtrinsicConvention = "world_to_camera",
    [string]$TempDir = "",
    [switch]$NoPreload,
    [switch]$Cpu,
    [switch]$NoGpu
)

$ErrorActionPreference = "Stop"

$repoRoot = Resolve-Path (Join-Path $PSScriptRoot "..\..")

function Convert-ToContainerPath([string]$PathText) {
    $resolved = Resolve-Path -LiteralPath $PathText
    $repoPath = $repoRoot.Path.TrimEnd("\", "/")
    $resolvedPath = $resolved.Path
    if (
        -not $resolvedPath.Equals($repoPath, [System.StringComparison]::OrdinalIgnoreCase) -and
        -not $resolvedPath.StartsWith("$repoPath\", [System.StringComparison]::OrdinalIgnoreCase)
    ) {
        throw "Path must be inside the repository because only the repository is mounted: $($resolved.Path)"
    }
    $relative = $resolvedPath.Substring($repoPath.Length).TrimStart("\", "/")
    return "/workspace/gc-image-processing/" + ($relative -replace "\\", "/")
}

$containerCalib = Convert-ToContainerPath $CalibJson
$effectiveDevice = if ($Cpu) { "cpu" } else { $Device }

$dockerArgs = @(
    "run",
    "--rm",
    "-p", "$($HttpPort):9000",
    "-p", "$($GrpcPort):50051",
    "-v", "$($repoRoot.Path):/workspace/gc-image-processing",
    "-w", "/workspace/gc-image-processing",
    "-e", "PROCESSING_HTTP_HOST=0.0.0.0",
    "-e", "PROCESSING_HTTP_PORT=9000",
    "-e", "PROCESSING_GRPC_BIND=0.0.0.0:50051",
    "-e", "PROCESSING_PROCESSOR=mmpose_triangulation",
    "-e", "PROCESSING_MMPOSE_CALIB_JSON=$containerCalib",
    "-e", "PROCESSING_MMPOSE_CAMERA_MAPPING=$($CameraMapping -join ',')",
    "-e", "PROCESSING_MMPOSE_POSE2D=$Pose2d",
    "-e", "PROCESSING_MMPOSE_DEVICE=$effectiveDevice",
    "-e", "PROCESSING_MMPOSE_KPT_THR=$KptThr",
    "-e", "PROCESSING_MMPOSE_MAX_REPROJ_ERROR=$MaxReprojError",
    "-e", "PROCESSING_MMPOSE_IMAGES_UNDISTORTED=$($ImagesUndistorted.IsPresent.ToString().ToLowerInvariant())",
    "-e", "PROCESSING_MMPOSE_EXTRINSIC_SOURCE=$ExtrinsicSource",
    "-e", "PROCESSING_MMPOSE_EXTRINSIC_CONVENTION=$ExtrinsicConvention",
    "-e", "PROCESSING_MMPOSE_PRELOAD=$((-not $NoPreload.IsPresent).ToString().ToLowerInvariant())"
)

if ($TempDir) {
    $dockerArgs += @("-e", "PROCESSING_MMPOSE_TEMP_DIR=/workspace/gc-image-processing/$($TempDir -replace "\\", "/")")
}

if (-not $NoGpu -and -not $Cpu) {
    $dockerArgs += @("--gpus", "all")
}

$dockerArgs += @(
    $Image,
    "--host", "0.0.0.0",
    "--port", "9000",
    "--grpc-bind", "0.0.0.0:50051",
    "--processor", "mmpose_triangulation",
    "--mmpose-calib-json", $containerCalib,
    "--mmpose-pose2d", $Pose2d,
    "--mmpose-device", $effectiveDevice,
    "--mmpose-kpt-thr", "$KptThr",
    "--mmpose-max-reproj-error", "$MaxReprojError",
    "--mmpose-extrinsic-source", $ExtrinsicSource,
    "--mmpose-extrinsic-convention", $ExtrinsicConvention
)

foreach ($mapping in $CameraMapping) {
    $dockerArgs += @("--mmpose-camera-mapping", $mapping)
}

if ($ImagesUndistorted) {
    $dockerArgs += "--mmpose-images-undistorted"
}

if (-not $NoPreload) {
    $dockerArgs += "--mmpose-preload"
}

if ($TempDir) {
    $dockerArgs += @("--mmpose-temp-dir", "/workspace/gc-image-processing/$($TempDir -replace "\\", "/")")
}

Write-Host "Running MMPose processing server"
Write-Host "Image:   $Image"
Write-Host "HTTP:    http://localhost:$HttpPort"
Write-Host "gRPC:    localhost:$GrpcPort"
Write-Host "Calib:   $containerCalib"
Write-Host "Device:  $effectiveDevice"
Write-Host "Mapping: $($CameraMapping -join ',')"
Write-Host "Preload: $((-not $NoPreload.IsPresent).ToString().ToLowerInvariant())"

docker @dockerArgs
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}
