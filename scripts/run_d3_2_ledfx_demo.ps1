$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
Set-StrictMode -Version Latest

$repo = "C:\Users\brian\dreamsync"
Set-Location $repo

$baseUrl = "http://127.0.0.1:8888"
$virtualId = "dreamsync-check"
$deviceName = "dreamsync-dummy"
$wavPath = Join-Path $repo "out\d3_2_demo.wav"
$jsonlPath = Join-Path $repo "out\d3_2_ledfx_replay.jsonl"

Write-Host "Starting/validating LedFx..."
& powershell -ExecutionPolicy Bypass -File ".\start_ledfx.ps1"

Write-Host "Ensuring dreamsync module is importable..."
& py -3.12 -c "import dreamsync; print(dreamsync.__version__)" *> $null
if ($LASTEXITCODE -ne 0) {
  throw "dreamsync is not importable in py -3.12 environment."
}

Write-Host "Generating deterministic demo WAV..."
& py -3.12 ".\scripts\generate_d3_2_demo_wav.py" --out $wavPath
if ($LASTEXITCODE -ne 0) {
  throw "WAV generation failed."
}

Write-Host "Ensuring target virtual/device mapping exists..."
$v = Invoke-RestMethod -Method Get -Uri "$baseUrl/api/virtuals"
$hasVirtual = $false
if ($v.virtuals) {
  $hasVirtual = @($v.virtuals.PSObject.Properties.Name) -contains $virtualId
}
if (-not $hasVirtual) {
  $vBody = @{
    config = @{
      name = $virtualId
      mapping = "span"
      grouping = 1
      rows = 1
      frequency_min = 20
      frequency_max = 15000
      max_brightness = 1.0
      center_offset = 0
      preview_only = $true
    }
  } | ConvertTo-Json -Depth 8
  $vCreate = Invoke-RestMethod -Method Post -Uri "$baseUrl/api/virtuals" -ContentType "application/json" -Body $vBody
  if ($vCreate.virtual.id -ne $virtualId) {
    throw "Unexpected virtual id returned: $($vCreate.virtual.id)"
  }
}

$d = Invoke-RestMethod -Method Get -Uri "$baseUrl/api/devices"
$deviceId = $null
if ($d.devices) {
  foreach ($prop in $d.devices.PSObject.Properties) {
    if ($prop.Value.type -eq "dummy" -and $prop.Value.config.name -eq $deviceName) {
      $deviceId = $prop.Name
      break
    }
  }
}
if (-not $deviceId) {
  $dBody = @{
    type = "dummy"
    config = @{
      name = $deviceName
      pixel_count = 30
    }
  } | ConvertTo-Json -Depth 6
  $dCreate = Invoke-RestMethod -Method Post -Uri "$baseUrl/api/devices" -ContentType "application/json" -Body $dBody
  $deviceId = $dCreate.device.id
}

$segBody = @{
  segments = @(
    @($deviceId, 0, 29, $false)
  )
} | ConvertTo-Json -Depth 8 -Compress
Invoke-RestMethod -Method Post -Uri "$baseUrl/api/virtuals/$virtualId" -ContentType "application/json" -Body $segBody | Out-Null

Write-Host "Running D3.2 replay command..."
$resultLines = & py -3.12 -m dreamsync ledfx-replay $wavPath --base-url $baseUrl --virtual-id $virtualId --realtime --jsonl $jsonlPath
if ($LASTEXITCODE -ne 0) {
  throw "ledfx-replay command failed."
}
$resultLines | ForEach-Object { Write-Host $_ }

if (-not (Test-Path $jsonlPath)) {
  throw "Replay JSONL log file was not created: $jsonlPath"
}
$lineCount = (Get-Content -Path $jsonlPath | Measure-Object -Line).Lines
if ($lineCount -lt 1) {
  throw "Replay JSONL log file is empty."
}

$summaryRaw = ($resultLines | Select-Object -Last 1)
$summary = $summaryRaw | ConvertFrom-Json
if ($summary.rows -lt 1) {
  throw "Replay summary rows was < 1."
}
if ($summary.sent -lt 1) {
  throw "Replay summary sent was < 1."
}

$modeKeys = @()
if ($summary.mode_counts) {
  $modeKeys = @($summary.mode_counts.PSObject.Properties.Name)
}
if ($modeKeys.Count -lt 2) {
  throw "Expected >=2 modes during replay, got: $($modeKeys -join ',')"
}

$verification = @{
  verified = $true
  virtual_id = $virtualId
  device_id = $deviceId
  wav_path = $wavPath
  jsonl_path = $jsonlPath
  rows = $summary.rows
  sent = $summary.sent
  mode_counts = $summary.mode_counts
  log_lines = $lineCount
}

Write-Host ""
Write-Host "D3.2 verification summary:"
$verification | ConvertTo-Json -Depth 10
