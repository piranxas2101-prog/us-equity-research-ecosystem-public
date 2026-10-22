param(
    [string]$PythonExe = (Join-Path $env:LOCALAPPDATA "Programs\Python\Python312\python.exe"),
    [int]$PollSeconds = 300,
    [int]$MaxCycles = 84,
    [double]$NotionalPerTrade = 250.0,
    [double]$StopLossPct = 0.03,
    [double]$TakeProfitPct = 0.06,
    [string]$HoldingPeriodRule = "same_day_flat",
    [string]$EntryTimeframe = "15Min",
    [string]$ApprovedIntents = "output\koulis_order_intents_latest.json",
    [switch]$NoStartMonitors,
    [switch]$PreviewOnly
)

$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$logsDir = Join-Path $projectRoot "logs"
$outputDir = Join-Path $projectRoot "output"
$timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
$tradingWindowPath = Join-Path $projectRoot "trading_window_config.json"

New-Item -ItemType Directory -Force -Path $logsDir | Out-Null
New-Item -ItemType Directory -Force -Path $outputDir | Out-Null

$tradingWindow = $null
if (Test-Path $tradingWindowPath) {
    $tradingWindow = Get-Content -Path $tradingWindowPath -Raw | ConvertFrom-Json
}

$stdoutLog = Join-Path $logsDir "miltos_entry_stalker_$timestamp.out.log"
$stderrLog = Join-Path $logsDir "miltos_entry_stalker_$timestamp.err.log"

$args = @(
    ".\miltos_entry_stalker.py",
    "--yankos-report", "output\yankos_paper_trades_latest.json",
    "--periklis-report", "output\periklis_paper_trades_latest.json",
    "--out-prefix", "output\miltos_entry_stalker",
    "--state-file", "output\miltos_entry_stalker_state.json",
    "--trading-window-config", "trading_window_config.json",
    "--entry-timeframe", $EntryTimeframe,
    "--notional-per-trade", [string]$NotionalPerTrade,
    "--poll-seconds", [string]$PollSeconds,
    "--max-cycles", [string]$MaxCycles
)

if (-not $PreviewOnly) {
    $args += "--submit"
    $args += "--approved-intents"
    $args += $ApprovedIntents
}

$process = Start-Process -FilePath $PythonExe -ArgumentList $args -WorkingDirectory $projectRoot -PassThru -WindowStyle Hidden -RedirectStandardOutput $stdoutLog -RedirectStandardError $stderrLog

$monitorsStarted = $false
if ((-not $PreviewOnly) -and (-not $NoStartMonitors)) {
    $monitorState = Join-Path $outputDir "trader_position_monitors_latest.json"
    $activeMonitorFound = $false
    if (Test-Path $monitorState) {
        try {
            $state = Get-Content -Path $monitorState -Raw | ConvertFrom-Json
            foreach ($monitor in @($state.monitors)) {
                if ($null -ne $monitor.pid -and (Get-Process -Id ([int]$monitor.pid) -ErrorAction SilentlyContinue)) {
                    $activeMonitorFound = $true
                    break
                }
            }
        } catch {
            $activeMonitorFound = $false
        }
    }

    if (-not $activeMonitorFound) {
        & "$projectRoot\start_trader_position_monitors_today.ps1" -PythonExe $PythonExe -StopLossPct $StopLossPct -TakeProfitPct $TakeProfitPct -HoldingPeriodRule $HoldingPeriodRule
        $monitorsStarted = $true
    }
}

$payload = [pscustomobject]@{
    run_id = $env:ECOSYSTEM_RUN_ID
    started_at = (Get-Date).ToString("o")
    agent = "miltos"
    pid = $process.Id
    preview_only = [bool]$PreviewOnly
    monitors_started = $monitorsStarted
    poll_seconds = $PollSeconds
    max_cycles = $MaxCycles
    risk_settings = [pscustomobject]@{
        notional_per_trade_usd = $NotionalPerTrade
        stop_loss_pct = $StopLossPct
        take_profit_pct = $TakeProfitPct
        holding_period_rule = $HoldingPeriodRule
    }
    entry_timeframe = $EntryTimeframe
    trading_window = $tradingWindow
    out_prefix = "output\miltos_entry_stalker"
    state_file = "output\miltos_entry_stalker_state.json"
    stdout_log = $stdoutLog
    stderr_log = $stderrLog
}

$latest = Join-Path $outputDir "miltos_entry_stalker_process_latest.json"
$payload | ConvertTo-Json -Depth 6 | Set-Content -Path $latest -Encoding UTF8

Write-Host "[MILTOS] pid=$($process.Id)"
Write-Host "[OUTPUT] $latest"
Write-Host "[LOG] $stdoutLog"
