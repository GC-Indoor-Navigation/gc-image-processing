param(
    [string]$EnvFile = "runtime\calibration.env",
    [string]$SessionId,
    [string]$JobsRoot,
    [string]$Camera1SourceDir,
    [string]$Camera2SourceDir,
    [string]$Camera3SourceDir,
    [int]$MaxImagesPerCamera = 0,
    [switch]$Overwrite,
    [switch]$NoSidecars
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

function Get-Images {
    param([string]$SourceDir)

    $exts = @(".jpg", ".jpeg", ".png", ".bmp", ".webp")
    $files = Get-ChildItem -LiteralPath $SourceDir -File |
        Where-Object { $exts -contains $_.Extension.ToLowerInvariant() } |
        Sort-Object Name

    if ($MaxImagesPerCamera -gt 0) {
        $files = $files | Select-Object -First $MaxImagesPerCamera
    }
    return @($files)
}

function Copy-Sidecars {
    param(
        [System.IO.FileInfo]$Image,
        [string]$TargetDir
    )

    if ($NoSidecars) {
        return
    }

    $candidates = @(
        "$($Image.FullName).metadata.json",
        "$($Image.DirectoryName)\$($Image.BaseName).metadata.json",
        "$($Image.FullName).json"
    )

    foreach ($candidate in $candidates) {
        if (Test-Path -LiteralPath $candidate) {
            Copy-Item -LiteralPath $candidate -Destination $TargetDir -Force:$Overwrite
        }
    }
}

$envValues = Read-EnvFile -Path $envPath

$sessionIdValue = Get-ConfigValue -Values $envValues -Key "CALIBRATION_SESSION_ID" -Override $SessionId
$jobsRootValue = Get-ConfigValue -Values $envValues -Key "CALIBRATION_JOBS_ROOT" -Override $JobsRoot
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

$jobsRootPath = Resolve-ConfigPath -PathValue $jobsRootValue
$jobRoot = Join-Path $jobsRootPath $sessionIdValue
$inputRoot = Join-Path $jobRoot "input"
$outputRoot = Join-Path $jobRoot "output"
$logsRoot = Join-Path $jobRoot "logs"

New-Item -ItemType Directory -Force -Path $inputRoot | Out-Null
New-Item -ItemType Directory -Force -Path $outputRoot | Out-Null
New-Item -ItemType Directory -Force -Path $logsRoot | Out-Null

$manifest = [ordered]@{
    session_id = $sessionIdValue
    input_dir = $inputRoot
    output_dir = $outputRoot
    logs_dir = $logsRoot
    cameras = @()
}

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

    $targetDir = Join-Path $inputRoot "camera$cameraNumber\camera${cameraNumber}_10fps"
    New-Item -ItemType Directory -Force -Path $targetDir | Out-Null

    $images = Get-Images -SourceDir $sourceDir
    if ($images.Count -eq 0) {
        throw "Camera${cameraNumber} source directory contains no supported images: $sourceDir"
    }

    foreach ($image in $images) {
        Copy-Item -LiteralPath $image.FullName -Destination $targetDir -Force:$Overwrite
        Copy-Sidecars -Image $image -TargetDir $targetDir
    }

    $manifest.cameras += [ordered]@{
        camera = "camera$cameraNumber"
        source_dir = $sourceDir
        target_dir = $targetDir
        image_count = $images.Count
    }
}

$manifestPath = Join-Path $jobRoot "source_manifest.json"
$manifest | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $manifestPath -Encoding UTF8

$jobPath = Join-Path $jobRoot "job.json"
if (-not (Test-Path -LiteralPath $jobPath) -or $Overwrite) {
    $job = [ordered]@{
        session_id = $sessionIdValue
        status = "PENDING"
        input_dir = $inputRoot
        output_dir = $outputRoot
        logs_dir = $logsRoot
        result_json = $null
        error = $null
    }
    $job | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $jobPath -Encoding UTF8
}

Write-Host "Prepared calibration real-session input"
Write-Host "Session:  $sessionIdValue"
Write-Host "JobRoot:  $jobRoot"
Write-Host "Manifest: $manifestPath"
foreach ($camera in $manifest.cameras) {
    Write-Host "$($camera.camera): $($camera.image_count) images"
}
