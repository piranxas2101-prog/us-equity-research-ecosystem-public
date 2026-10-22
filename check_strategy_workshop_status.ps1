$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $projectRoot

$pointerPath = ".\output\strategy_workshop\latest_background_daemon.json"
$statePath = ".\output\strategy_workshop\daemon_state.json"
$latestCyclePtr = ".\output\strategy_workshop\latest_cycle.txt"
$latestManifest = ".\output\strategy_workshop\latest_manifest.json"

if (-not (Test-Path $pointerPath)) {
    Write-Host "No strategy workshop background pointer found."
    exit 0
}

$pointer = Get-Content $pointerPath -Raw | ConvertFrom-Json
$daemonPid = [int]$pointer.pid

Write-Host "Pointer: $pointerPath"
Write-Host "Started at: $($pointer.started_at)"
Write-Host "PID: $daemonPid"
Write-Host "Out log: $($pointer.out_log)"
Write-Host "Err log: $($pointer.err_log)"

$proc = Get-Process -Id $daemonPid -ErrorAction SilentlyContinue
if ($null -eq $proc) {
    Write-Host "Status: not running"
} else {
    Write-Host "Status: running"
    Write-Host "CPU: $($proc.CPU)"
    Write-Host "StartTime: $($proc.StartTime)"
}

if (Test-Path $statePath) {
    $state = Get-Content $statePath -Raw | ConvertFrom-Json
    Write-Host ""
    Write-Host "--- Daemon state ---"
    Write-Host "status: $($state.status)"
    Write-Host "runs_completed: $($state.runs_completed)"
    Write-Host "max_runs: $($state.max_runs)"
    Write-Host "next_run_at: $($state.next_run_at)"
    if ($state.last_error) {
        Write-Host "last_error: $($state.last_error)"
    }
    if ($state.last_log) {
        Write-Host "last_log: $($state.last_log)"
    }
}

if (Test-Path $pointer.out_log) {
    Write-Host ""
    Write-Host "--- OUT (tail) ---"
    Get-Content $pointer.out_log -Tail 20
}
if (Test-Path $pointer.err_log) {
    Write-Host ""
    Write-Host "--- ERR (tail) ---"
    Get-Content $pointer.err_log -Tail 20
}

if (Test-Path $latestCyclePtr) {
    $latestCycle = [string](Get-Content $latestCyclePtr -Raw).Trim()
    if (-not [string]::IsNullOrWhiteSpace($latestCycle)) {
        Write-Host ""
        Write-Host "Latest completed cycle: $latestCycle"
        if (Test-Path $latestCycle) {
            Get-ChildItem $latestCycle | Sort-Object LastWriteTime | Select-Object Name,Length,LastWriteTime | Format-Table -AutoSize
        }
    }
}

if (Test-Path $latestManifest) {
    Write-Host ""
    Write-Host "Latest manifest: $latestManifest"
}
