param(
    [string]$Until = "14:30",
    [int]$IntervalMinutes = 30,
    [string]$OutputRoot = ".\output\strategy_workshop",
    [string]$PythonPath = (Join-Path $env:LOCALAPPDATA "Programs\Python\Python312\python.exe"),
    [string]$CompareFile = ".\output\scan_compare_strategy_upgrade_latest.json",
    [string]$YankosFile = ".\output\yankos_strategy_latest.json",
    [string]$PeriklisFile = ".\output\periklis_strategy_latest.json"
)

$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $projectRoot

$parts = $Until.Split(":")
if ($parts.Count -ne 2) {
    throw "Until must use HH:mm format, for example 14:30."
}

$now = Get-Date
$cutoff = Get-Date -Hour ([int]$parts[0]) -Minute ([int]$parts[1]) -Second 0
if ($cutoff -le $now) {
    throw "Cutoff $Until has already passed today."
}

$minutesRemaining = [Math]::Max(1, ($cutoff - $now).TotalMinutes)
$maxRuns = [Math]::Max(1, [int][Math]::Floor($minutesRemaining / $IntervalMinutes) + 1)

$logDir = Join-Path $projectRoot "logs"
if (-not (Test-Path $logDir)) {
    New-Item -ItemType Directory -Path $logDir -Force | Out-Null
}

$pointerPath = Join-Path $projectRoot "output\strategy_workshop\latest_background_daemon.json"
$pointerDir = Split-Path -Parent $pointerPath
if (-not (Test-Path $pointerDir)) {
    New-Item -ItemType Directory -Path $pointerDir -Force | Out-Null
}

if (Test-Path $pointerPath) {
    try {
        $existing = Get-Content $pointerPath -Raw | ConvertFrom-Json
        $existingPid = [int]$existing.pid
        $existingProc = Get-Process -Id $existingPid -ErrorAction SilentlyContinue
        if ($null -ne $existingProc) {
            Write-Host "Strategy workshop daemon already running (PID $existingPid)."
            exit 0
        }
    } catch {
    }
}

$ts = Get-Date -Format "yyyyMMdd_HHmmss"
$outLog = Join-Path $logDir "strategy_workshop_until_$ts.out.log"
$errLog = Join-Path $logDir "strategy_workshop_until_$ts.err.log"

$args = @(
    "-ExecutionPolicy", "Bypass",
    "-File", ".\run_strategy_workshop_daemon.ps1",
    "-IntervalMinutes", "$IntervalMinutes",
    "-MaxRuns", "$maxRuns",
    "-OutputRoot", $OutputRoot,
    "-PythonPath", $PythonPath,
    "-CompareFile", $CompareFile,
    "-YankosFile", $YankosFile,
    "-PeriklisFile", $PeriklisFile
)

$proc = Start-Process `
    -FilePath "C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe" `
    -ArgumentList $args `
    -WorkingDirectory $projectRoot `
    -RedirectStandardOutput $outLog `
    -RedirectStandardError $errLog `
    -WindowStyle Hidden `
    -PassThru

Start-Sleep -Seconds 2

$payload = [ordered]@{
    started_at = (Get-Date -Format o)
    pid = $proc.Id
    until = $cutoff.ToString("o")
    interval_minutes = $IntervalMinutes
    max_runs = $maxRuns
    python_path = $PythonPath
    out_log = $outLog
    err_log = $errLog
    state_file = ".\output\strategy_workshop\daemon_state.json"
}
$payload | ConvertTo-Json -Depth 6 | Set-Content -Path $pointerPath -Encoding UTF8

Write-Host "Strategy workshop daemon started in background until $Until."
Write-Host "PID: $($proc.Id)"
Write-Host "Max runs: $maxRuns"
Write-Host "Out log: $outLog"
Write-Host "Err log: $errLog"
Write-Host "Pointer: $pointerPath"
