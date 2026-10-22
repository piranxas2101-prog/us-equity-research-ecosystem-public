# Positions on their agent's horizon (ADR-001 step 3, owner 2026-10-23): starts alpaca_multi_day_monitor.py for
# today's session when data\paper_positions.json holds positions (or with -UntilClose, when entries are coming)
# and no such monitor is running. Called by start_trader_paper_trading_today.ps1 and by the afternoon session on
# days without new orders, so a position is watched every session until its agent's horizon ends. Paper only.
param(
    [string]$PythonExe = (Join-Path $env:LOCALAPPDATA "Programs\Python\Python312\python.exe"),
    # the trader launcher passes it when entries are coming: positions handed over during the day are watched
    [switch]$UntilClose
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$logsDir = Join-Path $projectRoot "logs"
New-Item -ItemType Directory -Force -Path $logsDir | Out-Null

$registry = Join-Path $projectRoot "data\paper_positions.json"
$count = 0
try { $count = @((Get-Content -LiteralPath $registry -Raw | ConvertFrom-Json).PSObject.Properties).Count } catch { }
if (($count -eq 0) -and (-not $UntilClose)) {
    Write-Host "[MULTI-DAY] No registered multi-day positions; monitor not started."
    return
}
$running = @(Get-CimInstance Win32_Process -Filter "Name like 'python%'" -ErrorAction SilentlyContinue |
    Where-Object { "$($_.CommandLine)" -match "alpaca_multi_day_monitor" })
if ($running.Count -gt 0) {
    Write-Host "[MULTI-DAY] Monitor already running (pid $($running[0].ProcessId))."
    return
}

$timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
$stdoutLog = Join-Path $logsDir "multi_day_monitor_$timestamp.out.log"
$stderrLog = Join-Path $logsDir "multi_day_monitor_$timestamp.err.log"
$monitorArgs = @(".\alpaca_multi_day_monitor.py", "--poll-seconds", "60", "--submit")
if ($UntilClose) { $monitorArgs += "--until-close" }
$process = Start-Process -FilePath $PythonExe -ArgumentList $monitorArgs `
    -WorkingDirectory $projectRoot -PassThru -WindowStyle Hidden -RedirectStandardOutput $stdoutLog -RedirectStandardError $stderrLog
Write-Host "[MULTI-DAY] Monitor started for $count position(s): pid $($process.Id), log $stdoutLog"
