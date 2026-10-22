$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $projectRoot

$pointer = ".\output\education_workshop\latest_background_run.json"
if (-not (Test-Path $pointer)) {
    Write-Host "No background workshop pointer found."
    exit 0
}

$run = Get-Content $pointer -Raw | ConvertFrom-Json
$runPid = [int]$run.pid

Write-Host "Pointer file: $pointer"
Write-Host "Started at: $($run.started_at)"
Write-Host "PID: $runPid"
Write-Host "Out log: $($run.out_log)"
Write-Host "Err log: $($run.err_log)"

$p = Get-Process -Id $runPid -ErrorAction SilentlyContinue
if ($null -eq $p) {
    Write-Host "Status: not running (process ended)"
} else {
    Write-Host "Status: running"
    Write-Host "CPU: $($p.CPU)"
    Write-Host "StartTime: $($p.StartTime)"
}

if (Test-Path $run.out_log) {
    Write-Host ""
    Write-Host "--- OUT (tail) ---"
    Get-Content $run.out_log -Tail 30
}
if (Test-Path $run.err_log) {
    Write-Host ""
    Write-Host "--- ERR (tail) ---"
    Get-Content $run.err_log -Tail 30
}

$latestRunPtr = ".\output\education_workshop\latest_run.txt"
if (Test-Path $latestRunPtr) {
    $runDir = [string](Get-Content $latestRunPtr -Raw).Trim()
    if (-not [string]::IsNullOrWhiteSpace($runDir) -and (Test-Path $runDir)) {
        Write-Host ""
        Write-Host "Latest completed run dir: $runDir"
        Get-ChildItem $runDir | Sort-Object LastWriteTime | Select-Object Name,Length,LastWriteTime | Format-Table -AutoSize
    }
}
