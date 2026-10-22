param(
    [string]$PythonExe = (Join-Path $env:LOCALAPPDATA "Programs\Python\Python312\python.exe"),
    [switch]$AutoFlattenExisting,
    [int]$MaxOrders = 6,
    [double]$MinScore = 7.0,
    [double]$NotionalPerTrade = 250.0,
    [double]$MaxTotalNotional = 0.0,
    [ValidateSet("default", "user_setting", "agent_suggestion")]
    [string]$NotionalSource = "default",
    [double]$TakeProfitPct = 0.06,
    [ValidateSet("default", "user_setting", "agent_suggestion")]
    [string]$TakeProfitSource = "default",
    [double]$StopLossPct = 0.03,
    [ValidateSet("default", "user_setting", "agent_suggestion")]
    [string]$StopLossSource = "default",
    [string]$HoldingPeriodRule = "same_day_flat",
    [ValidateSet("default", "user_setting", "agent_suggestion")]
    [string]$HoldingPeriodSource = "default",
    [string]$EntryTimeframe = "15Min",
    [int]$MaxShortOrders = 2,
    [switch]$NoStartMiltos,
    [int]$MiltosPollSeconds = 300,
    [int]$MiltosMaxCycles = 84,
    # The afternoon run passes the pre-open checked copies (output\preopen\*_strategy_preopen_latest.json).
    [string]$YankosStrategy = "output\yankos_strategy_latest.json",
    [string]$PeriklisStrategy = "output\periklis_strategy_latest.json"
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

function Run-Python {
    param(
        [string[]]$Arguments,
        [string]$StdoutLog,
        [string]$StderrLog
    )

    $process = Start-Process -FilePath $PythonExe -ArgumentList $Arguments -WorkingDirectory $projectRoot -Wait -PassThru -WindowStyle Hidden -RedirectStandardOutput $StdoutLog -RedirectStandardError $StderrLog
    return $process.ExitCode
}

$preflightOut = Join-Path $logsDir "alpaca_preflight_$timestamp.out.log"
$preflightErr = Join-Path $logsDir "alpaca_preflight_$timestamp.err.log"
$preflightArgs = @(
    ".\alpaca_account_preflight.py",
    "--out-prefix", "output\alpaca_preflight",
    "--block-on-existing"
)

if ($AutoFlattenExisting) {
    $preflightArgs += "--auto-flatten"
}

Write-Host "[PREFLIGHT] Starting Alpaca account preflight..."
$preflightExit = Run-Python -Arguments $preflightArgs -StdoutLog $preflightOut -StderrLog $preflightErr
if ($preflightExit -ne 0) {
    Write-Host "[PREFLIGHT] Blocked trader run. See $preflightOut and $preflightErr"
    exit $preflightExit
}
Write-Host "[PREFLIGHT] OK"

$effectiveMaxTotalNotional = $MaxTotalNotional
if ($effectiveMaxTotalNotional -le 0) {
    if (($NotionalSource -eq "agent_suggestion") -and (Test-Path "output\alpaca_preflight_latest.json")) {
        $preflightPayload = Get-Content "output\alpaca_preflight_latest.json" -Raw | ConvertFrom-Json
        $portfolioValue = [double]$preflightPayload.snapshot_before.account.portfolio_value
        $effectiveMaxTotalNotional = [Math]::Round([Math]::Max(0.0, $portfolioValue * 0.90), 2)
    } else {
        $effectiveMaxTotalNotional = $MaxOrders * $NotionalPerTrade
    }
}

$approvedIntents = Join-Path $outputDir "koulis_order_intents_latest.json"
$approvedIntentsArg = "output\koulis_order_intents_latest.json"
$koulisOut = Join-Path $logsDir "koulis_risk_gate_$timestamp.out.log"
$koulisErr = Join-Path $logsDir "koulis_risk_gate_$timestamp.err.log"
$koulisArgs = @(
    ".\koulis_risk_gate.py",
    "--yankos", $YankosStrategy,
    "--periklis", $PeriklisStrategy,
    "--preflight", "output\alpaca_preflight_latest.json",
    "--output", "output\koulis_order_intents_latest.json",
    "--max-total-orders", [string]$MaxOrders,
    "--max-short-orders", [string]$MaxShortOrders,
    "--min-score", [string]$MinScore,
    "--notional-per-trade", [string]$NotionalPerTrade,
    "--notional-source", $NotionalSource,
    "--max-total-notional", [string]$effectiveMaxTotalNotional
)
Write-Host "[KOULIS/THYMIOS] Building portfolio-approved intents..."
$koulisExit = Run-Python -Arguments $koulisArgs -StdoutLog $koulisOut -StderrLog $koulisErr
if ($koulisExit -ne 0) {
    Write-Host "[KOULIS/THYMIOS] Blocked trader run. See $koulisOut and $koulisErr"
    exit $koulisExit
}
$approvedIntentCount = 0
$approvedRunId = $env:ECOSYSTEM_RUN_ID
if (Test-Path $approvedIntents) {
    $approvedPayload = Get-Content -Path $approvedIntents -Raw | ConvertFrom-Json
    $approvedIntentCount = [int]$approvedPayload.approved_count
    $approvedRunId = [string]$approvedPayload.run_id
}

$traders = @(
    @{
        Name = "yankos"
        Strategy = $YankosStrategy
        OutPrefix = "output\yankos_paper_trades"
    },
    @{
        Name = "periklis"
        Strategy = $PeriklisStrategy
        OutPrefix = "output\periklis_paper_trades"
    }
)

$tradeRuns = @()
$submittedTotal = 0

foreach ($trader in $traders) {
    $stdoutLog = Join-Path $logsDir ("{0}_paper_trader_{1}.out.log" -f $trader.Name, $timestamp)
    $stderrLog = Join-Path $logsDir ("{0}_paper_trader_{1}.err.log" -f $trader.Name, $timestamp)
    $args = @(
        ".\alpaca_paper_trader.py",
        "--strategy", $trader.Strategy,
        "--out-prefix", $trader.OutPrefix,
        "--max-orders", [string]$MaxOrders,
        "--min-score", [string]$MinScore,
        "--notional-per-trade", [string]$NotionalPerTrade,
        "--notional-source", $NotionalSource,
        "--take-profit-pct", [string]$TakeProfitPct,
        "--take-profit-source", $TakeProfitSource,
        "--stop-loss-pct", [string]$StopLossPct,
        "--stop-loss-source", $StopLossSource,
        "--holding-period-rule", $HoldingPeriodRule,
        "--holding-period-source", $HoldingPeriodSource,
        "--entry-confirmation-timeframe", $EntryTimeframe,
        "--approved-intents", $approvedIntentsArg,
        "--exclude-existing-report",
        "--submit"
    )

    Write-Host ("[TRADER] Starting {0}..." -f $trader.Name)
    $exitCode = Run-Python -Arguments $args -StdoutLog $stdoutLog -StderrLog $stderrLog
    if ($exitCode -ne 0) {
        throw "Trader run failed for $($trader.Name). See $stdoutLog and $stderrLog"
    }

    $reportPath = Join-Path $projectRoot ("{0}_latest.json" -f $trader.OutPrefix)
    $submitted = 0
    if (Test-Path $reportPath) {
        $payload = Get-Content -Path $reportPath -Raw | ConvertFrom-Json
        $submitted = [int]$payload.submitted_count
    }
    $submittedTotal += $submitted
    $tradeRuns += [pscustomobject]@{
        agent = $trader.Name
        strategy = $trader.Strategy
        report = ("{0}_latest.json" -f $trader.OutPrefix)
        submitted_count = $submitted
        stdout_log = $stdoutLog
        stderr_log = $stderrLog
    }
    Write-Host ("[TRADER] {0} submitted={1}" -f $trader.Name, $submitted)
}

$monitorsStarted = $false
if ($submittedTotal -gt 0) {
    Write-Host "[MONITORS] Starting position monitors..."
    & "$projectRoot\start_trader_position_monitors_today.ps1" -PythonExe $PythonExe -StopLossPct $StopLossPct -TakeProfitPct $TakeProfitPct -HoldingPeriodRule $HoldingPeriodRule
    $monitorsStarted = $true
} else {
    Write-Host "[MONITORS] No submitted orders; monitors not started."
}

$miltosStarted = $false
if ((-not $NoStartMiltos) -and ($approvedIntentCount -gt 0)) {
    Write-Host "[MILTOS] Starting entry stalker..."
    & "$projectRoot\start_miltos_entry_stalker_today.ps1" -PythonExe $PythonExe -ApprovedIntents $approvedIntentsArg -PollSeconds $MiltosPollSeconds -MaxCycles $MiltosMaxCycles -NotionalPerTrade $NotionalPerTrade -StopLossPct $StopLossPct -TakeProfitPct $TakeProfitPct -HoldingPeriodRule $HoldingPeriodRule -EntryTimeframe $EntryTimeframe
    $miltosStarted = $true
}
elseif ($approvedIntentCount -eq 0) {
    Write-Host "[MILTOS] No approved intents; entry stalker not started."
    $miltosNoopOut = Join-Path $logsDir "miltos_noop_$timestamp.out.log"
    $miltosNoopErr = Join-Path $logsDir "miltos_noop_$timestamp.err.log"
    $miltosNoopArgs = @(
        ".\miltos_entry_stalker.py",
        "--out-prefix", "output\miltos_entry_stalker",
        "--no-op-run-id", $approvedRunId,
        "--no-op-reason", "no_approved_intents"
    )
    $miltosNoopExit = Run-Python -Arguments $miltosNoopArgs -StdoutLog $miltosNoopOut -StderrLog $miltosNoopErr
    if ($miltosNoopExit -ne 0) {
        Write-Host "[MILTOS] Failed to write no-op artifact. See $miltosNoopOut and $miltosNoopErr"
        exit $miltosNoopExit
    }
}
else {
    Write-Host "[MILTOS] Disabled by operator; writing no-op artifact."
    $miltosNoopOut = Join-Path $logsDir "miltos_noop_$timestamp.out.log"
    $miltosNoopErr = Join-Path $logsDir "miltos_noop_$timestamp.err.log"
    $miltosNoopArgs = @(
        ".\miltos_entry_stalker.py",
        "--out-prefix", "output\miltos_entry_stalker",
        "--no-op-run-id", $approvedRunId,
        "--no-op-reason", "disabled_by_operator"
    )
    $miltosNoopExit = Run-Python -Arguments $miltosNoopArgs -StdoutLog $miltosNoopOut -StderrLog $miltosNoopErr
    if ($miltosNoopExit -ne 0) {
        Write-Host "[MILTOS] Failed to write no-op artifact. See $miltosNoopOut and $miltosNoopErr"
        exit $miltosNoopExit
    }
}

$payload = [pscustomobject]@{
    run_id = $env:ECOSYSTEM_RUN_ID
    started_at = (Get-Date).ToString("o")
    auto_flatten_existing = [bool]$AutoFlattenExisting
    max_orders = $MaxOrders
    min_score = $MinScore
    notional_per_trade = $NotionalPerTrade
    max_total_notional = $effectiveMaxTotalNotional
    risk_settings = [pscustomobject]@{
        notional_per_trade_usd = $NotionalPerTrade
        notional_source = $NotionalSource
        take_profit_pct = $TakeProfitPct
        take_profit_source = $TakeProfitSource
        stop_loss_pct = $StopLossPct
        stop_loss_source = $StopLossSource
        holding_period_rule = $HoldingPeriodRule
        holding_period_source = $HoldingPeriodSource
    }
    entry_timeframe = $EntryTimeframe
    trading_window = $tradingWindow
    preflight_report = "output\alpaca_preflight_latest.json"
    approved_intents = "output\koulis_order_intents_latest.json"
    approved_intent_count = $approvedIntentCount
    submitted_total = $submittedTotal
    monitors_started = $monitorsStarted
    miltos_started = $miltosStarted
    traders = $tradeRuns
}

$latest = Join-Path $outputDir "trader_paper_trading_run_latest.json"
$payload | ConvertTo-Json -Depth 6 | Set-Content -Path $latest -Encoding UTF8
Write-Host "[OUTPUT] $latest"
