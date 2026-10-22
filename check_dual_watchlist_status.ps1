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

Write-Host "Pointer: $pointerPath"
Write-Host "Started at: $($pointer.started_at)"
Write-Host "PID: $pidValue"
Write-Host "Locked watchlist: $($pointer.locked_watchlist)"
if ($null -ne $pointer.full_market) {
    Write-Host "Full market: $($pointer.full_market)"
}
if ($null -ne $pointer.meeting_duration_minutes) {
    Write-Host "Meeting duration minutes: $($pointer.meeting_duration_minutes)"
}
Write-Host "Out log: $($pointer.out_log)"
Write-Host "Err log: $($pointer.err_log)"

$proc = Get-Process -Id $pidValue -ErrorAction SilentlyContinue
if ($null -eq $proc) {
    Write-Host "Status: not running"
} else {
    Write-Host "Status: running"
    Write-Host "CPU: $($proc.CPU)"
    Write-Host "StartTime: $($proc.StartTime)"
}

if (Test-Path $pointer.out_log) {
    Write-Host ""
    Write-Host "--- OUT (tail) ---"
    Get-Content $pointer.out_log -Tail 40
}
if (Test-Path $pointer.err_log) {
    Write-Host ""
    Write-Host "--- ERR (tail) ---"
    Get-Content $pointer.err_log -Tail 40
}

Write-Host ""
Write-Host "--- Latest dual watchlist outputs ---"
Get-ChildItem .\output -Filter "dual_watchlist_summary_*.md" |
    Sort-Object LastWriteTime -Descending |
    Select-Object -First 5 Name,Length,LastWriteTime |
    Format-Table -AutoSize

if (Test-Path ".\output\giannis_rule_based_latest.md") {
    Write-Host ""
    Write-Host "Giannis latest: .\output\giannis_rule_based_latest.md"
}
if (Test-Path ".\output\babis_shortlist_latest.json") {
    Write-Host "Babis latest: .\output\babis_shortlist_latest.json"
}
