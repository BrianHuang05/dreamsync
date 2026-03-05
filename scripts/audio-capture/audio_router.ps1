# audio_router.ps1 - Toggle system audio routing to/from VB-Audio Virtual Cable
# Usage:
#   .\audio_router.ps1 start   - Route default output to CABLE Input, save previous device
#   .\audio_router.ps1 stop    - Restore previous default output device
#   .\audio_router.ps1 status  - Show current default output device

param(
    [Parameter(Position=0)]
    [ValidateSet('start', 'stop', 'status')]
    [string]$Action = 'status'
)

# Add PolicyConfig COM interface for setting default audio device
Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;

[ComImport]
[Guid("F8679F50-850A-41CF-9C72-430F290290C8")]
[InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
interface IPolicyConfig
{
    [PreserveSig] int GetMixFormat(string pszDeviceName, IntPtr ppFormat);
    [PreserveSig] int GetDeviceFormat(string pszDeviceName, bool bDefault, IntPtr ppFormat);
    [PreserveSig] int ResetDeviceFormat(string pszDeviceName);
    [PreserveSig] int SetDeviceFormat(string pszDeviceName, IntPtr pEndpointFormat, IntPtr mixFormat);
    [PreserveSig] int GetProcessingPeriod(string pszDeviceName, bool bDefault, IntPtr pmftDefaultPeriod, IntPtr pmftMinimumPeriod);
    [PreserveSig] int SetProcessingPeriod(string pszDeviceName, IntPtr pmftPeriod);
    [PreserveSig] int GetShareMode(string pszDeviceName, IntPtr pMode);
    [PreserveSig] int SetShareMode(string pszDeviceName, IntPtr mode);
    [PreserveSig] int GetPropertyValue(string pszDeviceName, bool bFxStore, IntPtr key, IntPtr pv);
    [PreserveSig] int SetPropertyValue(string pszDeviceName, bool bFxStore, IntPtr key, IntPtr pv);
    [PreserveSig] int SetDefaultEndpoint(string pszDeviceName, int eRole);
    [PreserveSig] int SetEndpointVisibility(string pszDeviceName, bool bVisible);
}

[ComImport]
[Guid("870AF99C-171D-4F9E-AF0D-E63DF40C2BC9")]
class PolicyConfigClient {}

public class AudioSwitcher
{
    public static void SetDefaultDevice(string deviceId)
    {
        var policyConfig = (IPolicyConfig)new PolicyConfigClient();
        // eRole: 0=eConsole, 1=eMultimedia, 2=eCommunications
        policyConfig.SetDefaultEndpoint(deviceId, 0);
        policyConfig.SetDefaultEndpoint(deviceId, 1);
    }
}
'@ -ErrorAction Stop

$stateFile = Join-Path $PSScriptRoot '.audio_route_state.json'

function Get-AudioEndpoints {
    $endpoints = @()
    $renderKeys = Get-ChildItem "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\MMDevices\Audio\Render" -ErrorAction SilentlyContinue
    foreach ($key in $renderKeys) {
        $propPath = Join-Path $key.PSPath "Properties"
        $devState = (Get-ItemProperty $key.PSPath -ErrorAction SilentlyContinue).DeviceState
        if ($devState -ne 1) { continue }  # 1 = DEVICE_STATE_ACTIVE
        if (Test-Path $propPath) {
            $props = Get-ItemProperty $propPath -ErrorAction SilentlyContinue
            $name = $props.'{a45c254e-df1c-4efd-8020-67d146a850e0},2'
            $desc = $props.'{b3f8fa53-0004-438e-9003-51a46e139bfc},6'
            $deviceId = $key.PSChildName
            if ($name) {
                $endpoints += [PSCustomObject]@{
                    Id = "{0.0.0.00000000}.$deviceId"
                    Name = $name
                    Description = $desc
                    RegistryId = $deviceId
                }
            }
        }
    }
    return $endpoints
}

function Get-DefaultPlaybackDeviceId {
    $regPath = "HKCU:\SOFTWARE\Microsoft\Multimedia\Sound Mapper"
    $default = (Get-ItemProperty $regPath -ErrorAction SilentlyContinue).Playback
    if ($default) { return $default }

    # Fallback: check the UserChoice registry
    $endpoints = Get-AudioEndpoints
    foreach ($ep in $endpoints) {
        if ($ep.Name -notlike '*CABLE*') {
            return $ep.Id
        }
    }
    return $null
}

function Find-CableInputId {
    $endpoints = Get-AudioEndpoints
    $cable = $endpoints | Where-Object { $_.Name -like '*CABLE Input*' }
    if ($cable) { return $cable.Id }
    return $null
}

switch ($Action) {
    'status' {
        $endpoints = Get-AudioEndpoints
        Write-Output "Active playback devices:"
        foreach ($ep in $endpoints) {
            Write-Output "  [$($ep.Id)] $($ep.Name) ($($ep.Description))"
        }
        if (Test-Path $stateFile) {
            $state = Get-Content $stateFile | ConvertFrom-Json
            Write-Output ""
            Write-Output "Routing active: YES"
            Write-Output "Previous device: $($state.previousDeviceId) ($($state.previousDeviceName))"
        } else {
            Write-Output ""
            Write-Output "Routing active: NO"
        }
    }

    'start' {
        $cableId = Find-CableInputId
        if (-not $cableId) {
            Write-Error "CABLE Input device not found. Is VB-Audio Virtual Cable installed?"
            exit 1
        }

        # Save current default before switching
        $endpoints = Get-AudioEndpoints
        $currentNonCable = $endpoints | Where-Object { $_.Name -notlike '*CABLE*' } | Select-Object -First 1

        $state = @{
            previousDeviceId = $currentNonCable.Id
            previousDeviceName = $currentNonCable.Name
            cableDeviceId = $cableId
            startedAt = (Get-Date -Format o)
        }
        $state | ConvertTo-Json | Set-Content $stateFile

        Write-Output "Switching default output to CABLE Input ($cableId)..."
        [AudioSwitcher]::SetDefaultDevice($cableId)
        Write-Output "Audio routing STARTED. System audio now flows to virtual cable."
        Write-Output "Previous device saved: $($currentNonCable.Name)"
    }

    'stop' {
        if (-not (Test-Path $stateFile)) {
            Write-Output "No active routing session found."
            exit 0
        }

        $state = Get-Content $stateFile | ConvertFrom-Json
        Write-Output "Restoring default output to: $($state.previousDeviceName) ($($state.previousDeviceId))..."
        [AudioSwitcher]::SetDefaultDevice($state.previousDeviceId)
        Remove-Item $stateFile -Force
        Write-Output "Audio routing STOPPED. Default output restored."
    }
}
