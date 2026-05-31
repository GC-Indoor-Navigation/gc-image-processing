param(
    [string]$SessionId = "smoke",
    [string]$JobsRoot = "calibration_jobs",
    [int]$FrameCount = 3,
    [long]$StartTimestampMs = 1778825591815,
    [int]$StepMs = 100,
    [switch]$Overwrite
)

$ErrorActionPreference = "Stop"

if ($FrameCount -lt 1) {
    throw "FrameCount must be >= 1"
}

$repoRoot = Resolve-Path (Join-Path $PSScriptRoot "..\..")
$jobRoot = Join-Path (Join-Path $repoRoot $JobsRoot) $SessionId
$inputRoot = Join-Path $jobRoot "input"

if ((Test-Path -LiteralPath $inputRoot) -and $Overwrite) {
    $resolvedInput = (Resolve-Path -LiteralPath $inputRoot).Path
    $resolvedRepo = $repoRoot.Path
    if (-not $resolvedInput.StartsWith($resolvedRepo, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to remove path outside repo: $resolvedInput"
    }
    Remove-Item -LiteralPath $resolvedInput -Recurse -Force
}

for ($camera = 1; $camera -le 3; $camera++) {
    $cameraDir = Join-Path $inputRoot "camera$camera\camera$($camera)_10fps"
    New-Item -ItemType Directory -Force -Path $cameraDir | Out-Null

    for ($frame = 0; $frame -lt $FrameCount; $frame++) {
        $timestamp = $StartTimestampMs + ($frame * $StepMs)
        $imageName = "{0}_android_device_{1:D3}_camera_{2:D2}_{3}.jpg" -f $timestamp, $camera, $camera, $frame
        $imagePath = Join-Path $cameraDir $imageName
        New-Item -ItemType File -Force -Path $imagePath | Out-Null

        $metadata = [ordered]@{
            metadata = [ordered]@{
                device_timestamp_ms = $timestamp
                timestamp_sec = [double]($timestamp / 1000.0)
                frame_sequence = $frame
                sensor_timestamp_ns = $timestamp * 1000000
            }
        }
        $metadataPath = "$imagePath.metadata.json"
        $metadata | ConvertTo-Json -Depth 5 | Set-Content -Path $metadataPath -Encoding UTF8
    }
}

Write-Host "Created calibration smoke input"
Write-Host "Session: $SessionId"
Write-Host "Input:   $inputRoot"
Write-Host "Frames per camera: $FrameCount"
