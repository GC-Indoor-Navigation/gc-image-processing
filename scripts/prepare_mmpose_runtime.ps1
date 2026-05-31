param(
    [string]$CalibJson = "last_capstone\B\outputs\vggt_extrinsics_for_voxelpose\voxelpose_calibration_cascalib_baseline.json",
    [string[]]$CameraFrame = @(
        "camera1=last_capstone\B\images_260520\camera1.jpg",
        "camera2=last_capstone\B\images_260520\camera2.jpg",
        "camera3=last_capstone\B\images_260520\camera3.jpg"
    ),
    [string]$RuntimeDir = "runtime"
)

$ErrorActionPreference = "Stop"

$repoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$runtimeRoot = Join-Path $repoRoot $RuntimeDir
$calibrationDir = Join-Path $runtimeRoot "calibration"
$framesDir = Join-Path $runtimeRoot "smoke_frames"
$outputsDir = Join-Path $runtimeRoot "outputs\smoke"
$tmpDir = Join-Path $runtimeRoot "tmp\mmpose"

New-Item -ItemType Directory -Force -Path $calibrationDir | Out-Null
New-Item -ItemType Directory -Force -Path $framesDir | Out-Null
New-Item -ItemType Directory -Force -Path $outputsDir | Out-Null
New-Item -ItemType Directory -Force -Path $tmpDir | Out-Null

function Resolve-InputPath([string]$PathText) {
    if ([System.IO.Path]::IsPathRooted($PathText)) {
        return Resolve-Path -LiteralPath $PathText
    }
    return Resolve-Path -LiteralPath (Join-Path $repoRoot $PathText)
}

$sourceCalib = Resolve-InputPath $CalibJson
$targetCalib = Join-Path $calibrationDir "active_calibration.json"
Copy-Item -LiteralPath $sourceCalib.Path -Destination $targetCalib -Force

$preparedFrames = @()
foreach ($frameSpec in $CameraFrame) {
    $parts = $frameSpec.Split("=", 2)
    if ($parts.Count -ne 2) {
        throw "CameraFrame must be device_id=image_path: $frameSpec"
    }

    $deviceId = $parts[0]
    $sourceFrame = Resolve-InputPath $parts[1]
    $extension = [System.IO.Path]::GetExtension($sourceFrame.Path)
    if (-not $extension) {
        $extension = ".jpg"
    }

    $targetFrame = Join-Path $framesDir "$deviceId$extension"
    Copy-Item -LiteralPath $sourceFrame.Path -Destination $targetFrame -Force
    $preparedFrames += "$deviceId=$targetFrame"
}

Write-Host "Prepared MMPose runtime files"
Write-Host "Calibration: $targetCalib"
Write-Host "Frames:"
foreach ($frame in $preparedFrames) {
    Write-Host "  $frame"
}
Write-Host "Outputs:     $outputsDir"
Write-Host "Temp:        $tmpDir"
