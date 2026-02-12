param(
  [string]$JsonlPath = "C:\Users\brian\dreamsync\out\ledfx_live_proof.jsonl",
  [string]$StdoutPath = "C:\Users\brian\dreamsync\out\ledfx_live_proof_stdout.txt",
  [int]$MinFrameRows = 100,
  [int]$MinTelemetryRows = 3,
  [int]$MinSentTrue = 1,
  [int]$MinModeChanges = 1
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
Set-StrictMode -Version Latest

if (-not (Test-Path $JsonlPath)) {
  throw "Missing JSONL proof file: $JsonlPath"
}
if (-not (Test-Path $StdoutPath)) {
  throw "Missing stdout proof file: $StdoutPath"
}

$rows = Get-Content -Path $JsonlPath | ForEach-Object { $_ | ConvertFrom-Json }
$frameRows = @($rows | Where-Object { $_.kind -eq "frame" })
$telemetryRows = @($rows | Where-Object { $_.kind -eq "telemetry" })

if ($frameRows.Count -lt $MinFrameRows) {
  throw "frame row count too low: $($frameRows.Count) < $MinFrameRows"
}
if ($telemetryRows.Count -lt $MinTelemetryRows) {
  throw "telemetry row count too low: $($telemetryRows.Count) < $MinTelemetryRows"
}

$sentTrueRows = @($frameRows | Where-Object { $_.sent -eq $true }).Count
if ($sentTrueRows -lt $MinSentTrue) {
  throw "sent=true row count too low: $sentTrueRows < $MinSentTrue"
}

$modeChanges = 0
$lastMode = $null
foreach ($row in $frameRows) {
  $mode = [string]$row.mode
  if ($null -ne $lastMode -and $mode -ne $lastMode) {
    $modeChanges++
  }
  $lastMode = $mode
}
if ($modeChanges -lt $MinModeChanges) {
  throw "mode change count too low: $modeChanges < $MinModeChanges"
}

$sampleMin = ($telemetryRows | Measure-Object -Property samples_captured -Minimum).Minimum
$sampleMax = ($telemetryRows | Measure-Object -Property samples_captured -Maximum).Maximum
if ($sampleMax -le $sampleMin) {
  throw "samples_captured did not increase (min=$sampleMin max=$sampleMax)"
}

$summary = @{
  verified = $true
  jsonl_path = $JsonlPath
  stdout_path = $StdoutPath
  frame_rows = $frameRows.Count
  telemetry_rows = $telemetryRows.Count
  sent_true_rows = $sentTrueRows
  mode_changes = $modeChanges
  samples_captured_min = $sampleMin
  samples_captured_max = $sampleMax
}

$summary | ConvertTo-Json -Depth 8
