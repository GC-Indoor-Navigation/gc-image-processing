param(
    [Parameter(Mandatory = $true)]
    [string]$CalibJson,

    [Parameter(Mandatory = $true)]
    [string[]]$CameraFrame,

    [string]$OutJson = "mmpose_smoke_runs\latest\result.json",
    [string]$Python = "",
    [string]$Pose2d = "human",
    [string]$Device = "cuda:0",
    [double]$KptThr = 0.30,
    [double]$MaxReprojError = 40.0,
    [switch]$ImagesUndistorted,
    [string]$ExtrinsicSource = "auto",
    [string]$ExtrinsicConvention = "world_to_camera",
    [string]$TempDir = ""
)

$ErrorActionPreference = "Stop"

$repoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$runner = Join-Path $repoRoot "tools\mmpose_triangulation\run_smoke.py"

if (-not (Test-Path -LiteralPath $runner)) {
    throw "Smoke runner not found: $runner"
}

if (-not (Test-Path -LiteralPath $CalibJson)) {
    throw "Calibration JSON not found: $CalibJson"
}

if (-not $Python) {
    $venvPython = Join-Path $repoRoot ".venv\Scripts\python.exe"
    if (Test-Path -LiteralPath $venvPython) {
        $Python = $venvPython
    } else {
        $Python = "python"
    }
}

$argsList = @(
    $runner,
    "--calib-json", (Resolve-Path -LiteralPath $CalibJson).Path,
    "--out-json", $OutJson,
    "--pose2d", $Pose2d,
    "--device", $Device,
    "--kpt-thr", $KptThr,
    "--max-reproj-error", $MaxReprojError,
    "--extrinsic-source", $ExtrinsicSource,
    "--extrinsic-convention", $ExtrinsicConvention
)

foreach ($frame in $CameraFrame) {
    $argsList += @("--camera-frame", $frame)
}

if ($ImagesUndistorted) {
    $argsList += "--images-undistorted"
}

if ($TempDir) {
    $argsList += @("--temp-dir", $TempDir)
}

Write-Host "Running MMPose triangulation smoke"
Write-Host "Python:     $Python"
Write-Host "CalibJson:  $CalibJson"
Write-Host "OutJson:    $OutJson"
Write-Host "Pose2d:     $Pose2d"
Write-Host "Device:     $Device"
Write-Host "Frames:"
foreach ($frame in $CameraFrame) {
    Write-Host "  $frame"
}

& $Python @argsList
