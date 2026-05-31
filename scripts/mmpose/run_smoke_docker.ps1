param(
    [Parameter(Mandatory = $true)]
    [string]$CalibJson,

    [Parameter(Mandatory = $true)]
    [string[]]$CameraFrame,

    [string]$Image = "gc-mmpose-processing:smoke",
    [string]$OutJson = "runtime\outputs\smoke\result.json",
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

$repoRoot = Resolve-Path (Join-Path $PSScriptRoot "..\..")
$calibPath = Resolve-Path -LiteralPath $CalibJson

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

$containerCalib = Convert-ToContainerPath $calibPath.Path
$containerOut = "/workspace/gc-image-processing/" + ($OutJson -replace "\\", "/")

$runnerArgs = @(
    "tools/mmpose_triangulation/run_smoke.py",
    "--calib-json", $containerCalib,
    "--out-json", $containerOut,
    "--pose2d", $Pose2d,
    "--device", $(if ($Cpu) { "cpu" } else { $Device }),
    "--kpt-thr", $KptThr,
    "--max-reproj-error", $MaxReprojError,
    "--extrinsic-source", $ExtrinsicSource,
    "--extrinsic-convention", $ExtrinsicConvention
)

foreach ($frame in $CameraFrame) {
    $parts = $frame.Split("=", 3)
    if ($parts.Count -ne 3) {
        throw "CameraFrame must be device_id=CalibrationCameraName=image_path: $frame"
    }
    $containerImage = Convert-ToContainerPath $parts[2]
    $runnerArgs += @("--camera-frame", "$($parts[0])=$($parts[1])=$containerImage")
}

if ($ImagesUndistorted) {
    $runnerArgs += "--images-undistorted"
}

if ($TempDir) {
    $runnerArgs += @("--temp-dir", ("/workspace/gc-image-processing/" + ($TempDir -replace "\\", "/")))
}

$dockerArgs = @(
    "run",
    "--rm",
    "-v", "$($repoRoot.Path):/workspace/gc-image-processing",
    "-w", "/workspace/gc-image-processing",
    "--entrypoint", "python"
)

if (-not $NoGpu -and -not $Cpu) {
    $dockerArgs += @("--gpus", "all")
}

$dockerArgs += $Image
$dockerArgs += $runnerArgs

Write-Host "Running MMPose Docker smoke"
Write-Host "Image:    $Image"
Write-Host "Calib:    $containerCalib"
Write-Host "OutJson:  $containerOut"
Write-Host "Device:   $(if ($Cpu) { "cpu" } else { $Device })"

docker @dockerArgs
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}
