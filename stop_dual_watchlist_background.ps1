$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $projectRoot

$pointerPath = ".\output\dual_watchlist_latest_background.json"
if (-not (Test-Path $pointerPath)) {
    Write-Host "No dual watchlist background pointer found."
    exit 0
}

$pointer = Get-Content $pointerPath -Raw | ConvertFrom-Json
$pidValue = [int]$pointer.pid
$proc = Get-Process -Id $pidValue -ErrorAction SilentlyContinue
if ($null -eq $proc) {
    Write-Host "Dual watchlist background process is not running."
    exit 0
}

Stop-Process -Id $pidValue -Force
Write-Host "Stopped dual watchlist background process PID $pidValue."
