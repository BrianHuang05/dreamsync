# Check for VB-Audio Virtual Cable endpoints
Write-Output "=== Sound Devices ==="
Get-CimInstance Win32_SoundDevice | Select-Object Name, Status | Format-Table -AutoSize

Write-Output "=== Checking MMDevice endpoints via registry ==="

Write-Output "Playback endpoints with CABLE:"
$renderKeys = Get-ChildItem "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\MMDevices\Audio\Render" -ErrorAction SilentlyContinue
foreach ($key in $renderKeys) {
    $propPath = Join-Path $key.PSPath "Properties"
    if (Test-Path $propPath) {
        $props = Get-ItemProperty $propPath -ErrorAction SilentlyContinue
        $name = $props.'{a45c254e-df1c-4efd-8020-67d146a850e0},2'
        if ($name -and $name -like '*CABLE*') {
            Write-Output "  FOUND: $name"
        }
    }
}

Write-Output "Recording endpoints with CABLE:"
$captureKeys = Get-ChildItem "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\MMDevices\Audio\Capture" -ErrorAction SilentlyContinue
foreach ($key in $captureKeys) {
    $propPath = Join-Path $key.PSPath "Properties"
    if (Test-Path $propPath) {
        $props = Get-ItemProperty $propPath -ErrorAction SilentlyContinue
        $name = $props.'{a45c254e-df1c-4efd-8020-67d146a850e0},2'
        if ($name -and $name -like '*CABLE*') {
            Write-Output "  FOUND: $name"
        }
    }
}
