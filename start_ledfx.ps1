# LedFx bootstrap + health check + virtual ID listing
# Usage:
#   powershell -ExecutionPolicy Bypass -File .\start_ledfx.ps1

$ErrorActionPreference = "Stop"
$baseUrl = "http://127.0.0.1:8888"
$apiVirtuals = "$baseUrl/api/virtuals"
$timeoutSec = 60

function Invoke-External {
  param(
    [Parameter(Mandatory = $true)][string]$FilePath,
    [Parameter(Mandatory = $false)][string[]]$ArgumentList = @()
  )
  & $FilePath @ArgumentList
  if ($LASTEXITCODE -ne 0) {
    throw "Command failed: $FilePath $($ArgumentList -join ' ') (exit $LASTEXITCODE)"
  }
}

function Test-LedFxModuleInstalled {
  param(
    [Parameter(Mandatory = $false)][string]$PySelector = ""
  )

  # Use cmd redirection to avoid PowerShell promoting native stderr to terminating errors.
  if ($PySelector -ne "") {
    & cmd /c "py $PySelector -m pip show ledfx >nul 2>nul"
  } else {
    & cmd /c "py -m pip show ledfx >nul 2>nul"
  }
  return ($LASTEXITCODE -eq 0)
}

function Resolve-PythonLauncherSelector {
  if (-not (Get-Command py -ErrorAction SilentlyContinue)) {
    throw "Python launcher 'py' not found. Install Python from python.org, then retry."
  }

  $candidates = @("-3.12", "-3.11", "")

  foreach ($candidate in $candidates) {
    if ($candidate -ne "") {
      & py $candidate --version *> $null
    } else {
      & py --version *> $null
    }
    if ($LASTEXITCODE -eq 0) {
      return $candidate
    }
  }

  throw "No usable Python interpreter found via 'py'."
}

function Test-LedFxApi {
  try {
    Invoke-RestMethod -Method Get -Uri $apiVirtuals -TimeoutSec 3 | Out-Null
    return $true
  } catch {
    return $false
  }
}

# 1) If already up, skip launch
if (Test-LedFxApi) {
  Write-Host "LedFx already running at $baseUrl"
} else {
  # 2) Pick a Python version with better LedFx wheel compatibility.
  $pySelector = Resolve-PythonLauncherSelector
  $pySelectorDisplay = if ($pySelector -ne "") { $pySelector } else { "(default)" }
  Write-Host ("Using Python launcher: py " + $pySelectorDisplay)

  # 3) Install LedFx if command/module not available
  Write-Host "Checking LedFx module..."
  $hasLedFxModule = Test-LedFxModuleInstalled -PySelector $pySelector
  if (-not $hasLedFxModule) {
    Write-Host "Installing LedFx with selected Python..."
    if ($pySelector -ne "") {
      Invoke-External -FilePath "py" -ArgumentList @($pySelector, "-m", "pip", "install", "--user", "ledfx")
    } else {
      Invoke-External -FilePath "py" -ArgumentList @("-m", "pip", "install", "--user", "ledfx")
    }
  }

  # 4) Start LedFx in background
  Write-Host "Starting LedFx..."
  if ($pySelector -ne "") {
    Start-Process -FilePath "py" -ArgumentList @($pySelector, "-m", "ledfx") -WindowStyle Normal
  } else {
    Start-Process -FilePath "py" -ArgumentList @("-m", "ledfx") -WindowStyle Normal
  }

  # 5) Wait for API to come up
  $started = $false
  $sw = [Diagnostics.Stopwatch]::StartNew()
  while ($sw.Elapsed.TotalSeconds -lt $timeoutSec) {
    if (Test-LedFxApi) { $started = $true; break }
    Start-Sleep -Milliseconds 750
  }
  if (-not $started) {
    throw "LedFx did not become reachable at $baseUrl within $timeoutSec seconds."
  }
  Write-Host "LedFx is up at $baseUrl"
}

# 6) Print virtuals and IDs
$resp = Invoke-RestMethod -Method Get -Uri $apiVirtuals
Write-Host "`nRaw /api/virtuals response:"
$resp | ConvertTo-Json -Depth 8

Write-Host "`nDetected virtual IDs:"
if ($resp.virtuals) {
  $resp.virtuals.PSObject.Properties.Name | ForEach-Object { Write-Host "- $_" }
} elseif ($resp.PSObject.Properties.Name -contains "result" -and $resp.result.virtuals) {
  $resp.result.virtuals.PSObject.Properties.Name | ForEach-Object { Write-Host "- $_" }
} else {
  Write-Host "No virtuals found. Create one in LedFx UI, then re-run."
}
