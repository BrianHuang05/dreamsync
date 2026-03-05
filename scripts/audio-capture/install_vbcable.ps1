$downloadDir = Join-Path $env:USERPROFILE 'Downloads'
$zipPath = Join-Path $downloadDir 'VBCable_Driver_Pack43.zip'
$extractDir = Join-Path $downloadDir 'VBCable'

# Download VB-Cable
Write-Output "Downloading VB-Audio Virtual Cable..."
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
Invoke-WebRequest -Uri 'https://download.vb-audio.com/Download_CABLE/VBCABLE_Driver_Pack43.zip' -OutFile $zipPath -UseBasicParsing

Write-Output "Download complete. File size: $((Get-Item $zipPath).Length) bytes"
Write-Output "Extracting..."
if (Test-Path $extractDir) { Remove-Item -Recurse -Force $extractDir }
Expand-Archive -Path $zipPath -DestinationPath $extractDir -Force

Write-Output "Extracted. Contents:"
Get-ChildItem $extractDir | Format-Table Name, Length -AutoSize
