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
    [switch]$Cpu,
    [switch]$NoGpu
)

$ErrorActionPreference = "Stop"

$repoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")

function Convert-ToContainerPath([string]$PathText) {
    $resolved = Resolve-Path -LiteralPath $PathText
    $relative = [System.IO.Path]::GetRelativePath($repoRoot.Path, $resolved.Path)
    if ($relative.StartsWith("..")) {
        throw "Path must be inside the repository because only the repository is mounted: $($resolved.Path)"
    }
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
    "-e", "PROCESSING_MMPOSE_EXTRINSIC_CONVENTION=$ExtrinsicConvention"
)

if ($TempDir) {
    $dockerArgs += @("-e", "PROCESSING_MMPOSE_TEMP_DIR=/workspace/gc-image-processing/$($TempDir -replace "\\", "/")")
}

if (-not $NoGpu -and -not $Cpu) {
    $dockerArgs += @("--gpus", "all")
}

$dockerArgs += $Image

Write-Host "Running MMPose processing server"
Write-Host "Image:   $Image"
Write-Host "HTTP:    http://localhost:$HttpPort"
Write-Host "gRPC:    localhost:$GrpcPort"
Write-Host "Calib:   $containerCalib"
Write-Host "Device:  $effectiveDevice"
Write-Host "Mapping: $($CameraMapping -join ',')"

docker @dockerArgs
