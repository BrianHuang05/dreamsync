param(
  [int]$DurationSeconds = 20,
  [string]$BaseUrl = "http://127.0.0.1:8888",
  [string]$VirtualId = "dreamsync-check",
  [int]$DeviceId = -1
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
Set-StrictMode -Version Latest

$repo = "C:\Users\brian\dreamsync"
Set-Location $repo

$jsonlPath = Join-Path $repo "out\ledfx_live_proof.jsonl"
$stdoutPath = Join-Path $repo "out\ledfx_live_proof_stdout.txt"

Write-Host "Starting/validating LedFx..."
& powershell -ExecutionPolicy Bypass -File ".\start_ledfx.ps1"

Write-Host "Choosing input device..."
$deviceArg = @()
if ($DeviceId -ge 0) {
  $deviceArg = @("--device", "$DeviceId")
  Write-Host "Using explicit device id: $DeviceId"
} else {
  Write-Host "Using system default input device."
}

Write-Host "Running live pipeline (audio -> director -> ledfx)..."
$cmd = @(
  "py", "-3.12", "-m", "dreamsync", "ledfx-live",
  "--duration", "$DurationSeconds",
  "--base-url", $BaseUrl,
  "--virtual-id", $VirtualId,
  "--heartbeat-seconds", "1.0",
  "--jsonl", $jsonlPath
) + $deviceArg

$raw = & $cmd[0] @($cmd[1..($cmd.Count - 1)])
if ($LASTEXITCODE -ne 0) {
  throw "ledfx-live command failed."
}
$raw | Set-Content -Path $stdoutPath -Encoding UTF8
$raw | ForEach-Object { Write-Host $_ }

if (-not (Test-Path $jsonlPath)) {
  throw "Missing JSONL proof file: $jsonlPath"
}

$rows = Get-Content -Path $jsonlPath | ForEach-Object { $_ | ConvertFrom-Json }
$frameRows = @($rows | Where-Object { $_.kind -eq "frame" })
$telemetryRows = @($rows | Where-Object { $_.kind -eq "telemetry" })
if ($frameRows.Count -lt 20) {
  throw "Insufficient frame evidence. frame rows=$($frameRows.Count)"
}
if ($telemetryRows.Count -lt 2) {
  throw "Insufficient telemetry evidence. telemetry rows=$($telemetryRows.Count)"
}

$lastSummary = ($raw | Select-Object -Last 1) | ConvertFrom-Json
if ($lastSummary.samples_captured -le 0) {
  throw "No live audio samples captured."
}
if ($lastSummary.sent -le 0) {
  throw "No LedFx commands were sent."
}
if ($lastSummary.rows -le 0) {
  throw "No frame rows were processed."
}

$modeKeys = @()
if ($lastSummary.mode_counts) {
  $modeKeys = @($lastSummary.mode_counts.PSObject.Properties.Name)
}
if ($modeKeys.Count -lt 1) {
  throw "No state-machine modes recorded."
}

$sentTrue = @($frameRows | Where-Object { $_.sent -eq $true }).Count
$maxSamples = ($telemetryRows | Measure-Object -Property samples_captured -Maximum).Maximum
$minSamples = ($telemetryRows | Measure-Object -Property samples_captured -Minimum).Minimum

$proof = @{
  verified = $true
  duration_seconds = $DurationSeconds
  jsonl_path = $jsonlPath
  stdout_path = $stdoutPath
  frame_rows = $frameRows.Count
  telemetry_rows = $telemetryRows.Count
  sent_true_rows = $sentTrue
  samples_captured_min = $minSamples
  samples_captured_max = $maxSamples
  summary = $lastSummary
}

Write-Host ""
Write-Host "LIVE PROOF SUMMARY:"
$proof | ConvertTo-Json -Depth 12
