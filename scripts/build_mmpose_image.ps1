param(
    [string]$Image = "gc-mmpose-processing:smoke",
    [string]$Dockerfile = "docker\mmpose\Dockerfile",
    [string]$BaseImage = "nvidia/cuda:12.1.1-cudnn8-devel-ubuntu22.04",
    [switch]$NoCache
)

$ErrorActionPreference = "Stop"

$repoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$dockerfilePath = Resolve-Path (Join-Path $repoRoot $Dockerfile)

$argsList = @(
    "build",
    "-f", $dockerfilePath.Path,
    "-t", $Image,
    "--build-arg", "BASE_IMAGE=$BaseImage"
)

if ($NoCache) {
    $argsList += "--no-cache"
}

$argsList += $repoRoot.Path

Write-Host "Building MMPose processing image"
Write-Host "Image:      $Image"
Write-Host "Dockerfile: $($dockerfilePath.Path)"
Write-Host "Context:    $($repoRoot.Path)"
Write-Host "BaseImage:  $BaseImage"

docker @argsList
