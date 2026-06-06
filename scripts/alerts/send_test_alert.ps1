param(
    [Parameter(Mandatory = $true)]
    [string]$TargetUrl,

    [ValidateSet("info", "warning", "danger")]
    [string]$Severity = "danger",

    [string]$EventId = "",
    [int]$FrameSetId = 1,
    [int]$RelayRunId = 1,
    [double]$DistanceM = 0.2,
    [string]$Joint = "left_ankle",
    [string]$ObstacleId = "test-obstacle",
    [int]$TtlMs = 3000,
    [string]$Processor = "test_alert_sender",
    [string[]]$CameraDevice = @(
        "android_device_001",
        "android_device_002",
        "android_device_003"
    ),
    [int]$TimeoutSec = 2,
    [int]$Repeat = 1,
    [double]$IntervalSec = 1.0
)

$ErrorActionPreference = "Stop"

if (-not $EventId) {
    $EventId = "test-alert-$([guid]::NewGuid().ToString('N').Substring(0, 12))"
}

for ($index = 0; $index -lt $Repeat; $index++) {
    $timestampMs = [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds()
    $payload = [ordered]@{
        event_id = if ($Repeat -eq 1) { $EventId } else { "$EventId-$($index + 1)" }
        frame_set_id = $FrameSetId + $index
        relay_run_id = $RelayRunId
        timestamp_ms = $timestampMs
        severity = $Severity
        distance_m = $DistanceM
        joint = $Joint
        obstacle_id = $ObstacleId
        ttl_ms = $TtlMs
        source = [ordered]@{
            processor = $Processor
            camera_devices = @($CameraDevice)
        }
    }

    $json = $payload | ConvertTo-Json -Depth 10
    Write-Host "[send] $json"
    Invoke-RestMethod `
        -Method Post `
        -Uri $TargetUrl `
        -ContentType "application/json" `
        -Body $json `
        -TimeoutSec $TimeoutSec | Out-Null
    Write-Host "[ok] sent event_id=$($payload.event_id)"

    if ($index -lt $Repeat - 1) {
        Start-Sleep -Seconds $IntervalSec
    }
}
