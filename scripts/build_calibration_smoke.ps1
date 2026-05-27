param(
    [string]$Image = "gc-calibration:smoke",
    [string]$BaseImage = "python:3.10-slim"
)

$ErrorActionPreference = "Stop"

$repoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$dockerfile = Join-Path $repoRoot "docker\calibration\Dockerfile"

if (-not (Test-Path -LiteralPath $dockerfile)) {
    throw "Dockerfile not found: $dockerfile"
}

$dockerArgs = @(
    "build",
    "--target", "smoke",
    "--build-arg", "BASE_IMAGE=$BaseImage",
    "-f", $dockerfile,
    "-t", $Image,
    $repoRoot
)

Write-Host "Building calibration smoke image: $Image"
Write-Host "Base image: $BaseImage"
& docker @dockerArgs
