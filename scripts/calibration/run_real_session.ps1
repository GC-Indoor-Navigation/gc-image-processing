param(
    [string]$EnvFile = "runtime\calibration.env",
    [string]$SessionId,
    [string]$JobsRoot,
    [string]$Image = "gc-calibration:full",
    [string]$Camera1SourceDir,
    [string]$Camera2SourceDir,
    [string]$Camera3SourceDir,
    [string]$MmposeDevice,
    [double]$PersonHeightM = 1.7,
    [double]$MaxDtMs = 80.0,
    [switch]$Gpu,
    [switch]$NoDryRun,
    [switch]$RunCascalib,
    [switch]$NoAutoMmpose
)

$ErrorActionPreference = "Stop"

$repoRoot = Resolve-Path (Join-Path $PSScriptRoot "..\..")
$envPath = Join-Path $repoRoot $EnvFile

function Read-EnvFile {
    param([string]$Path)

    $values = @{}
    if (-not (Test-Path -LiteralPath $Path)) {
        return $values
    }

    foreach ($rawLine in Get-Content -LiteralPath $Path) {
        $line = $rawLine.Trim()
        if ($line.Length -eq 0 -or $line.StartsWith("#")) {
            continue
        }

        $eq = $line.IndexOf("=")
        if ($eq -lt 1) {
            continue
        }

        $key = $line.Substring(0, $eq).Trim()
        $value = $line.Substring($eq + 1).Trim()
        if (
            ($value.StartsWith('"') -and $value.EndsWith('"')) -or
            ($value.StartsWith("'") -and $value.EndsWith("'"))
        ) {
            $value = $value.Substring(1, $value.Length - 2)
        }
        $values[$key] = $value
    }
    return $values
}

function Get-ConfigValue {
    param(
        [hashtable]$Values,
        [string]$Key,
        [string]$Override
    )

    if (-not [string]::IsNullOrWhiteSpace($Override)) {
        return $Override
    }
    if ($Values.ContainsKey($Key) -and -not [string]::IsNullOrWhiteSpace($Values[$Key])) {
        return $Values[$Key]
    }
    return $null
}

function Resolve-ConfigPath {
    param([string]$PathValue)

    if ([string]::IsNullOrWhiteSpace($PathValue)) {
        return $null
    }

    if ([System.IO.Path]::IsPathRooted($PathValue)) {
        return $PathValue
    }
    return (Join-Path $repoRoot $PathValue)
}

function Get-ImageCount {
    param([string]$SourceDir)

    $exts = @(".jpg", ".jpeg", ".png", ".bmp", ".webp")
    $count = (
        Get-ChildItem -LiteralPath $SourceDir -File |
            Where-Object { $exts -contains $_.Extension.ToLowerInvariant() } |
            Measure-Object
    ).Count
    return [int]$count
}

$envValues = Read-EnvFile -Path $envPath

$sessionIdValue = Get-ConfigValue -Values $envValues -Key "CALIBRATION_SESSION_ID" -Override $SessionId
$jobsRootValue = Get-ConfigValue -Values $envValues -Key "CALIBRATION_JOBS_ROOT" -Override $JobsRoot
$mmposeDeviceValue = Get-ConfigValue -Values $envValues -Key "CALIBRATION_MMPOSE_DEVICE" -Override $MmposeDevice
$cameraSources = @(
    Get-ConfigValue -Values $envValues -Key "CAMERA1_SOURCE_DIR" -Override $Camera1SourceDir
    Get-ConfigValue -Values $envValues -Key "CAMERA2_SOURCE_DIR" -Override $Camera2SourceDir
    Get-ConfigValue -Values $envValues -Key "CAMERA3_SOURCE_DIR" -Override $Camera3SourceDir
)

if ([string]::IsNullOrWhiteSpace($sessionIdValue)) {
    throw "Missing CALIBRATION_SESSION_ID. Set it in $EnvFile or pass -SessionId."
}
if ([string]::IsNullOrWhiteSpace($jobsRootValue)) {
    throw "Missing CALIBRATION_JOBS_ROOT. Set it in $EnvFile or pass -JobsRoot."
}
if ([string]::IsNullOrWhiteSpace($mmposeDeviceValue)) {
    $mmposeDeviceValue = "cpu"
}

$jobsRootPath = Resolve-ConfigPath -PathValue $jobsRootValue
$jobRoot = Join-Path $jobsRootPath $sessionIdValue
$outputRoot = Join-Path $jobRoot "output"
$logsRoot = Join-Path $jobRoot "logs"

New-Item -ItemType Directory -Force -Path $jobRoot | Out-Null
New-Item -ItemType Directory -Force -Path $outputRoot | Out-Null
New-Item -ItemType Directory -Force -Path $logsRoot | Out-Null

$resolvedCameraSources = @()
$manifestCameras = @()
for ($index = 0; $index -lt 3; $index++) {
    $cameraNumber = $index + 1
    $sourceValue = $cameraSources[$index]
    if ([string]::IsNullOrWhiteSpace($sourceValue)) {
        throw "Missing CAMERA${cameraNumber}_SOURCE_DIR. Set it in $EnvFile or pass -Camera${cameraNumber}SourceDir."
    }

    $sourceDir = Resolve-ConfigPath -PathValue $sourceValue
    if (-not (Test-Path -LiteralPath $sourceDir -PathType Container)) {
        throw "Camera${cameraNumber} source directory not found: $sourceDir"
    }

    $imageCount = Get-ImageCount -SourceDir $sourceDir
    if ($imageCount -eq 0) {
        throw "Camera${cameraNumber} source directory contains no supported images: $sourceDir"
    }

    $resolvedCameraSources += (Resolve-Path -LiteralPath $sourceDir).Path
    $manifestCameras += [ordered]@{
        camera = "camera$cameraNumber"
        source_dir = (Resolve-Path -LiteralPath $sourceDir).Path
        container_dir = "/data/input/camera$cameraNumber/camera${cameraNumber}_10fps"
        image_count = $imageCount
    }
}

$manifest = [ordered]@{
    session_id = $sessionIdValue
    input_mode = "direct_mount"
    output_dir = $outputRoot
    logs_dir = $logsRoot
    mmpose_device = $mmposeDeviceValue
    cameras = $manifestCameras
}
$manifestPath = Join-Path $jobRoot "source_manifest.json"
$manifest | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $manifestPath -Encoding UTF8

$jobPath = Join-Path $jobRoot "job.json"
$job = [ordered]@{
    session_id = $sessionIdValue
    status = "PENDING"
    input_mode = "direct_mount"
    output_dir = $outputRoot
    logs_dir = $logsRoot
    result_json = $null
    error = $null
}
$job | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $jobPath -Encoding UTF8

$outputPath = (Resolve-Path -LiteralPath $outputRoot).Path
$logsPath = (Resolve-Path -LiteralPath $logsRoot).Path

$dockerArgs = @("run", "--rm")

if ($Gpu) {
    $dockerArgs += @("--gpus", "all")
}

for ($index = 0; $index -lt 3; $index++) {
    $cameraNumber = $index + 1
    $hostPath = $resolvedCameraSources[$index]
    $containerPath = "/data/input/camera$cameraNumber/camera${cameraNumber}_10fps"
    $dockerArgs += @("-v", "${hostPath}:${containerPath}:ro")
}

$dockerArgs += @(
    "-v", "${outputPath}:/data/output",
    "-v", "${logsPath}:/data/logs",
    $Image,
    "--input", "/data/input",
    "--output", "/data/output",
    "--logs", "/data/logs",
    "--num-cameras", "3",
    "--person-height-m", "$PersonHeightM",
    "--max-dt-ms", "$MaxDtMs",
    "--mmpose-device", $mmposeDeviceValue,
    "--force"
)

if (-not $NoDryRun) {
    $dockerArgs += "--dry-run"
}

if (-not $RunCascalib) {
    $dockerArgs += "--skip-cascalib"
}

if ($NoAutoMmpose) {
    $dockerArgs += "--no-auto-mmpose"
}

Write-Host "Running calibration real-session job"
Write-Host "Session:      $sessionIdValue"
Write-Host "Image:        $Image"
Write-Host "Mode:         direct_mount"
Write-Host "MMPoseDevice: $mmposeDeviceValue"
Write-Host "Output:       $outputPath"
Write-Host "Logs:         $logsPath"
Write-Host "Manifest:     $manifestPath"
foreach ($camera in $manifestCameras) {
    Write-Host "$($camera.camera): $($camera.image_count) images from $($camera.source_dir)"
}

& docker @dockerArgs
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}
