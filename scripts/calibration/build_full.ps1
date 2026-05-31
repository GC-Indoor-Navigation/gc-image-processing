param(
    [string]$Image = "gc-calibration:full",
    [string]$MmposeBaseImage = "gc-mmpose-processing:smoke",
    [string]$Dockerfile = "docker\calibration\Dockerfile",
    [string]$PyTorch3DRef = "75ebeeaea0908c5527e7b1e305fbc7681382db47",
    [string]$TorchCudaArchList = "8.6",
    [int]$MaxJobs = 1,
    [switch]$NoCache
)

$ErrorActionPreference = "Stop"

$repoRoot = Resolve-Path (Join-Path $PSScriptRoot "..\..")
$dockerfilePath = Resolve-Path (Join-Path $repoRoot $Dockerfile)

$argsList = @(
    "build",
    "--target", "full",
    "-f", $dockerfilePath.Path,
    "-t", $Image,
    "--build-arg", "MMPOSE_BASE_IMAGE=$MmposeBaseImage",
    "--build-arg", "PYTORCH3D_REF=$PyTorch3DRef",
    "--build-arg", "TORCH_CUDA_ARCH_LIST=$TorchCudaArchList",
    "--build-arg", "MAX_JOBS=$MaxJobs"
)

if ($NoCache) {
    $argsList += "--no-cache"
}

$argsList += $repoRoot.Path

Write-Host "Building full calibration image"
Write-Host "Image:            $Image"
Write-Host "Dockerfile:       $($dockerfilePath.Path)"
Write-Host "Context:          $($repoRoot.Path)"
Write-Host "MMPoseBaseImage:  $MmposeBaseImage"
Write-Host "PyTorch3DRef:     $PyTorch3DRef"
Write-Host "TorchCUDAArch:    $TorchCudaArchList"
Write-Host "MaxJobs:          $MaxJobs"

docker @argsList
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}
