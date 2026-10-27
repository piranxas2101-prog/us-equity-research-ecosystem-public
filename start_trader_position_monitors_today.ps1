param(
    [string]$PythonExe = "python",
    [double]$StopLossPct = 0.03,
    [double]$TakeProfitPct = 0.06,
    [string]$HoldingPeriodRule = "same_day_flat"
)

$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
$logsDir = Join-Path $projectRoot "logs"
$outputDir = Join-Path $projectRoot "output"

New-Item -ItemType Directory -Force -Path $logsDir | Out-Null

$monitors = @(
    @{
        Name = "yankos"
        OpenReport = "output\yankos_paper_trades_latest.json"
        Strategy = "output\yankos_strategy_latest.json"
        OutPrefix = "output\yankos_position_monitor"
    },
    @{
        Name = "periklis"
        OpenReport = "output\periklis_paper_trades_latest.json"
        Strategy = "output\periklis_strategy_latest.json"
        OutPrefix = "output\periklis_position_monitor"
    }
)

$started = @()

foreach ($monitor in $monitors) {
    $stdoutLog = Join-Path $logsDir ("{0}_position_monitor_{1}.out.log" -f $monitor.Name, $timestamp)
    $stderrLog = Join-Path $logsDir ("{0}_position_monitor_{1}.err.log" -f $monitor.Name, $timestamp)
    $args = @(
        ".\alpaca_paper_position_monitor.py"
        "--open-report", $monitor.OpenReport
        "--strategy", $monitor.Strategy
        "--out-prefix", $monitor.OutPrefix
        "--poll-seconds", "60"
        "--stop-loss-pct", [string]$StopLossPct
        "--take-profit-pct", [string]$TakeProfitPct
        "--exit-score-floor", "4.75"
        "--close-on-watch"
        "--today-only-policy"
        "--eod-cutoff", "15:55"
        "--submit"
    )

    $process = Start-Process -FilePath $PythonExe -ArgumentList $args -WorkingDirectory $projectRoot -PassThru -WindowStyle Hidden -RedirectStandardOutput $stdoutLog -RedirectStandardError $stderrLog
    $started += [pscustomobject]@{
        agent = $monitor.Name
        pid = $process.Id
        open_report = $monitor.OpenReport
        strategy = $monitor.Strategy
        out_prefix = $monitor.OutPrefix
        stdout_log = $stdoutLog
        stderr_log = $stderrLog
    }
}

$payload = [pscustomobject]@{
    started_at = (Get-Date).ToString("o")
    risk_settings = [pscustomobject]@{
        stop_loss_pct = $StopLossPct
        take_profit_pct = $TakeProfitPct
        holding_period_rule = $HoldingPeriodRule
    }
    monitors = $started
}

$latest = Join-Path $outputDir "trader_position_monitors_latest.json"
$payload | ConvertTo-Json -Depth 6 | Set-Content -Path $latest -Encoding UTF8

Write-Host "[OUTPUT] $latest"
foreach ($item in $started) {
    Write-Host ("[MONITOR] {0} pid={1} out={2}" -f $item.agent, $item.pid, $item.stdout_log)
}

$reconcileOut = Join-Path $logsDir ("trader_reconciliation_waiter_{0}.out.log" -f $timestamp)
$reconcileErr = Join-Path $logsDir ("trader_reconciliation_waiter_{0}.err.log" -f $timestamp)
$reconcileArgs = @(
    "-ExecutionPolicy", "Bypass",
    "-File", ".\run_trader_reconciliation_today.ps1",
    # quoted: Start-Process joins the list with spaces, and "New project" split the path (no reconciliation
    # ran since 2026-08-26; found 2026-10-27)
    "-PythonExe", ('"{0}"' -f $PythonExe),
    "-SessionLabel", "after_monitors",
    "-WaitForMonitors"
)
$reconcileProcess = Start-Process -FilePath "C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe" -ArgumentList $reconcileArgs -WorkingDirectory $projectRoot -PassThru -WindowStyle Hidden -RedirectStandardOutput $reconcileOut -RedirectStandardError $reconcileErr
Write-Host ("[RECONCILIATION] waiter pid={0} out={1}" -f $reconcileProcess.Id, $reconcileOut)
