param(
    [string]$PythonExe = (Join-Path $env:LOCALAPPDATA "Programs\Python\Python312\python.exe"),
    [string]$SessionLabel = "manual",
    [switch]$WaitForMonitors,
    [int]$PollSeconds = 30,
    [int]$MaxWaitMinutes = 390
)

$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$outputDir = Join-Path $projectRoot "output"
$logsDir = Join-Path $projectRoot "logs"
$monitorStatePath = Join-Path $outputDir "trader_position_monitors_latest.json"
$timestamp = Get-Date -Format "yyyyMMdd_HHmmss"

New-Item -ItemType Directory -Force -Path $outputDir | Out-Null
New-Item -ItemType Directory -Force -Path $logsDir | Out-Null

function Active-Monitor-Found {
    if (-not (Test-Path $monitorStatePath)) {
        return $false
    }
    try {
        $state = Get-Content -Path $monitorStatePath -Raw | ConvertFrom-Json
        foreach ($monitor in @($state.monitors)) {
            if ($null -ne $monitor.pid -and (Get-Process -Id ([int]$monitor.pid) -ErrorAction SilentlyContinue)) {
                return $true
            }
        }
    } catch {
        return $false
    }
    return $false
}

if ($WaitForMonitors) {
    $deadline = (Get-Date).AddMinutes($MaxWaitMinutes)
    while ((Get-Date) -lt $deadline) {
        if (-not (Active-Monitor-Found)) {
            break
        }
        Start-Sleep -Seconds ([Math]::Max(10, $PollSeconds))
    }
}

$stdoutLog = Join-Path $logsDir "alpaca_order_reconciliation_$timestamp.out.log"
$stderrLog = Join-Path $logsDir "alpaca_order_reconciliation_$timestamp.err.log"
$args = @(
    ".\alpaca_order_reconciliation.py",
    "--yankos-report", "output\yankos_paper_trades_latest.json",
    "--periklis-report", "output\periklis_paper_trades_latest.json",
    "--out-prefix", "output\alpaca_order_reconciliation",
    "--session-label", $SessionLabel
)

$process = Start-Process -FilePath $PythonExe -ArgumentList $args -WorkingDirectory $projectRoot -Wait -PassThru -WindowStyle Hidden -RedirectStandardOutput $stdoutLog -RedirectStandardError $stderrLog
if ($process.ExitCode -ne 0) {
    throw "Trader reconciliation failed with exit code $($process.ExitCode). See $stdoutLog and $stderrLog"
}

Write-Host "[OUTPUT] output\alpaca_order_reconciliation_latest.json"
Write-Host "[OUTPUT] output\alpaca_order_reconciliation_latest.md"
Write-Host "[LOG] $stdoutLog"
