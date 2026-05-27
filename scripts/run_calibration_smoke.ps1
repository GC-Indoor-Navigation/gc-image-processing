param(
    [Parameter(Mandatory = $true)]
    [string]$SessionId,

    [string]$JobsRoot = "calibration_jobs",
    [string]$Image = "gc-calibration:smoke",
    [switch]$Gpu,
    [switch]$NoDryRun,
    [switch]$RunCascalib
)

$ErrorActionPreference = "Stop"

$repoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$jobsRootPath = Join-Path $repoRoot $JobsRoot
$jobRoot = Join-Path $jobsRootPath $SessionId
$inputDir = Join-Path $jobRoot "input"
$outputDir = Join-Path $jobRoot "output"
$logsDir = Join-Path $jobRoot "logs"

if (-not (Test-Path -LiteralPath $inputDir)) {
    throw "Input directory not found: $inputDir"
}

New-Item -ItemType Directory -Force -Path $outputDir | Out-Null
New-Item -ItemType Directory -Force -Path $logsDir | Out-Null

$inputPath = (Resolve-Path -LiteralPath $inputDir).Path
$outputPath = (Resolve-Path -LiteralPath $outputDir).Path
$logsPath = (Resolve-Path -LiteralPath $logsDir).Path

$dockerArgs = @("run", "--rm")

if ($Gpu) {
    $dockerArgs += @("--gpus", "all")
}

$dockerArgs += @(
    "-v", "${inputPath}:/data/input",
    "-v", "${outputPath}:/data/output",
    "-v", "${logsPath}:/data/logs",
    $Image,
    "--input", "/data/input",
    "--output", "/data/output",
    "--logs", "/data/logs",
    "--force"
)

if (-not $NoDryRun) {
    $dockerArgs += "--dry-run"
}

if (-not $RunCascalib) {
    $dockerArgs += "--skip-cascalib"
}

Write-Host "Running calibration smoke job"
Write-Host "Session: $SessionId"
Write-Host "Image:   $Image"
Write-Host "Input:   $inputPath"
Write-Host "Output:  $outputPath"
Write-Host "Logs:    $logsPath"

& docker @dockerArgs
