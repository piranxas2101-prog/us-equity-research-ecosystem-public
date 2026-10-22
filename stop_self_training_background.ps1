$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $projectRoot

$pointerPath = ".\output\self_training\latest_background_daemon.json"
if (-not (Test-Path $pointerPath)) {
    Write-Host "No self-training daemon pointer found."
    exit 0
}

$pointer = Get-Content $pointerPath -Raw | ConvertFrom-Json
$daemonPid = [int]$pointer.pid

$proc = Get-Process -Id $daemonPid -ErrorAction SilentlyContinue
if ($null -eq $proc) {
    Write-Host "Daemon is already stopped."
} else {
    Stop-Process -Id $daemonPid -Force
    Write-Host "Stopped self-training daemon PID $daemonPid."
}

$stoppedAt = (Get-Date -Format o)
$pointer | Add-Member -NotePropertyName stopped_at -NotePropertyValue $stoppedAt -Force
$pointer | ConvertTo-Json -Depth 6 | Set-Content -Path $pointerPath -Encoding UTF8
Write-Host "Updated pointer: $pointerPath"
