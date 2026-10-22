param(
    [string]$RunId = "",
    [string]$LockedWatchlistPath = ".\output\watchlist_300_locked.json",
    [switch]$RebuildLock,
    [switch]$FullMarket,
    [int]$FullMarketLimit = 0,
    [int]$FundamentalControlPlaneLimit = 5,
    [int]$MeetingDurationMinutes = 0,
    [switch]$SubmitPaper,
    [switch]$NoAutoTraderStart,
    [switch]$NoCompletionPopup,
    [string]$PythonPath = "",
    [int]$TraderMaxOrders = 6,
    [int]$TraderMaxShortOrders = 2,
    [double]$TraderMinScore = 7.0,
    [double]$TraderNotionalPerTrade = 250.0,
    [ValidateSet("default", "user_setting", "agent_suggestion")]
    [string]$TraderNotionalSource = "default",
    [double]$TraderTakeProfitPct = 0.06,
    [ValidateSet("default", "user_setting", "agent_suggestion")]
    [string]$TraderTakeProfitSource = "default",
    [double]$TraderStopLossPct = 0.03,
    [ValidateSet("default", "user_setting", "agent_suggestion")]
    [string]$TraderStopLossSource = "default",
    [string]$TraderHoldingPeriodRule = "same_day_flat",
    [ValidateSet("default", "user_setting", "agent_suggestion")]
    [string]$TraderHoldingPeriodSource = "default",
    [string]$TraderEntryTimeframe = "15Min",
    [int]$MiltosPollSeconds = 300,
    [int]$MiltosMaxCycles = 84
)

$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $projectRoot

function Resolve-PythonPath {
    $venvPython = Join-Path $projectRoot ".venv\Scripts\python.exe"
    if (Test-Path $venvPython) {
        return $venvPython
    }
    $globalPython = "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe"
    try {
        if (Test-Path $globalPython) {
            return $globalPython
        }
    } catch {
        # Fallback to PATH-based python when AppData access is restricted.
    }
    return "python"
}

function Build-LockedWatchlist {
    param(
        [string]$DestinationPath
    )
    $latestPath = Join-Path $projectRoot "output\babis_shortlist_latest.json"
    if (-not (Test-Path $latestPath)) {
        throw "Missing source shortlist: $latestPath"
    }
    $latest = Get-Content $latestPath -Raw | ConvertFrom-Json
    $rows = @($latest.shortlist | Select-Object -First 300)
    if ($rows.Count -eq 0) {
        throw "No shortlist rows found in $latestPath"
    }

    $tickers = @()
    foreach ($row in $rows) {
        $ticker = [string]$row.ticker
        if ([string]::IsNullOrWhiteSpace($ticker)) {
            continue
        }
        if ($tickers -contains $ticker) {
            continue
        }
        $tickers += $ticker
    }
    if ($tickers.Count -eq 0) {
        throw "Could not build locked watchlist tickers."
    }

    $payload = [ordered]@{
        run_timestamp = (Get-Date).ToString("o")
        source_shortlist = $latestPath
        count = $tickers.Count
        tickers = $tickers
    }

    $dest = Resolve-Path (Split-Path -Parent $DestinationPath) -ErrorAction SilentlyContinue
    if (-not $dest) {
        New-Item -ItemType Directory -Path (Split-Path -Parent $DestinationPath) -Force | Out-Null
    }
    $payload | ConvertTo-Json -Depth 5 | Set-Content -Path $DestinationPath -Encoding UTF8
    return $tickers
}

function Load-LockedWatchlist {
    param(
        [string]$Path
    )
    if (-not (Test-Path $Path)) {
        throw "Missing locked watchlist: $Path"
    }
    $data = Get-Content $Path -Raw | ConvertFrom-Json
    $tickers = @($data.tickers)
    if ($tickers.Count -eq 0) {
        throw "Locked watchlist has zero tickers: $Path"
    }
    return $tickers
}

function Backup-LatestArtifact {
    param(
        [string]$SourcePath,
        [string]$DestinationDir
    )
    if (-not (Test-Path $SourcePath)) {
        return $null
    }
    if (-not (Test-Path $DestinationDir)) {
        New-Item -ItemType Directory -Path $DestinationDir -Force | Out-Null
    }
    $destPath = Join-Path $DestinationDir ([System.IO.Path]::GetFileName($SourcePath))
    Copy-Item -LiteralPath $SourcePath -Destination $destPath -Force
    return $destPath
}

$outputDir = Join-Path $projectRoot "output"
$logDir = Join-Path $projectRoot "logs"
if (-not (Test-Path $outputDir)) { New-Item -ItemType Directory -Path $outputDir | Out-Null }
if (-not (Test-Path $logDir)) { New-Item -ItemType Directory -Path $logDir | Out-Null }

$pythonPath = if ([string]::IsNullOrWhiteSpace($PythonPath)) { Resolve-PythonPath } else { $PythonPath }
if (-not (Test-Path -LiteralPath $pythonPath)) {
    throw "Python runtime does not exist: $pythonPath"
}

Write-Host "Verifying versioned UNKNOWN reason taxonomy and agent field contracts..."
& $pythonPath "unknown_data.py" "verify" `
    "--contract" "unknown_data_contract.json"
if ($LASTEXITCODE -ne 0) {
    throw "Daily pipeline blocked: UNKNOWN reason taxonomy or agent field contract is invalid."
}

Write-Host "Verifying versioned professional personalities for all nine agents..."
& $pythonPath "agent_personality.py" "verify" `
    "--registry" "agent_personality_registry.json" `
    "--contract" "ecosystem_theory_contract.json"
if ($LASTEXITCODE -ne 0) {
    throw "Daily pipeline blocked: agent personality registry is invalid, stale, or disconnected from the theory contract."
}

Write-Host "Verifying versioned methodology registry for all nine agents..."
& $pythonPath "agent_methodology.py" "verify" `
    "--registry" "agent_methodology_registry.json" `
    "--contract" "ecosystem_theory_contract.json"
if ($LASTEXITCODE -ne 0) {
    throw "Daily pipeline blocked: agent methodology registry is invalid, stale, or disconnected from the theory contract."
}

Write-Host "Verifying theoretical agent-assimilation gate..."
& $pythonPath "agent_assimilation.py" "verify" `
    "--contract" "ecosystem_theory_contract.json" `
    "--reports-dir" "docs/agent_reports"
if ($LASTEXITCODE -ne 0) {
    throw "Daily run blocked: theoretical agent-assimilation reports are missing, stale, or inconsistent."
}

Write-Host "Running frozen practical personality-behavior rehearsal for all nine agents..."
& $pythonPath "agent_personality_behavior.py" `
    "--output-dir" "output/personality_behavior_assimilation_gate"
if ($LASTEXITCODE -ne 0) {
    throw "Daily run blocked: one or more agents failed the practical personality behavior or authority-boundary rehearsal."
}

Write-Host "Verifying trader playbook assimilation gate against current code hashes..."
& $pythonPath "trader_playbook_assimilation.py" `
    "--output-dir" "output/playbook_assimilation_gate"
if ($LASTEXITCODE -ne 0) {
    throw "Daily trader stages blocked: Yankos/Periklis playbook assimilation is stale or failed."
}

$timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
$ecosystemRunId = if ([string]::IsNullOrWhiteSpace($RunId)) { "run_$timestamp" } else { $RunId }
$env:ECOSYSTEM_RUN_ID = $ecosystemRunId
$pipelineLog = Join-Path $logDir "dual_watchlist_pipeline_$timestamp.log"
$giannisLog = Join-Path $logDir "dual_watchlist_giannis_$timestamp.log"
$baselineDir = Join-Path $outputDir "baseline_compare_$timestamp"
$compareJsonPath = Join-Path $outputDir "scan_compare_latest.json"
$compareMdPath = Join-Path $outputDir "scan_compare_latest.md"
$pipelineWarnings = New-Object System.Collections.Generic.List[string]
$traderHandoffStatus = "not_started"
$traderHandoffLog = Join-Path $logDir "dual_watchlist_trader_handoff_$timestamp.log"
$traderHandoffReport = Join-Path $outputDir "trader_paper_trading_run_latest.json"
$traderHandoffExitCode = $null

function Add-PipelineWarning {
    param([string]$Message)
    $pipelineWarnings.Add($Message) | Out-Null
    Write-Warning $Message
}

# Per-step timing: which agent works on which step, and for how long. The file is
# rewritten at every step boundary, so it also shows the step that is running now
# (or the one a failed run stopped in). Timing never blocks the pipeline.
$stepTimings = New-Object System.Collections.Generic.List[object]
$stepTimingsPath = Join-Path $outputDir "pipeline_step_timings_latest.json"
$stepTimingsRunPath = Join-Path $outputDir "pipeline_step_timings_$timestamp.json"
$script:currentStepStartedAt = $null

function Save-PipelineStepTimings {
    param([string]$CurrentStep)
    $payload = [ordered]@{
        schema = "ecosystem.pipeline_step_timings.v1"
        run_id = $ecosystemRunId
        updated_at = (Get-Date).ToString("o")
        current_step = $CurrentStep
        steps = $stepTimings.ToArray()
    }
    $json = $payload | ConvertTo-Json -Depth 5
    foreach ($path in @($stepTimingsRunPath, $stepTimingsPath)) {
        $temporary = "$path.tmp"
        Set-Content -LiteralPath $temporary -Value $json -Encoding UTF8
        Move-Item -LiteralPath $temporary -Destination $path -Force
    }
}

function Close-PipelineStep {
    if ($stepTimings.Count -eq 0 -or $null -eq $script:currentStepStartedAt) { return }
    $finished = Get-Date
    $last = $stepTimings[$stepTimings.Count - 1]
    $last.finished_at = $finished.ToString("o")
    $last.seconds = [math]::Round(($finished - $script:currentStepStartedAt).TotalSeconds, 1)
    $script:currentStepStartedAt = $null
}

function Mark-PipelineStep {
    param([string]$Key, [string]$Agent)
    try {
        Close-PipelineStep
        $script:currentStepStartedAt = Get-Date
        $stepTimings.Add([ordered]@{
            key = $Key; agent = $Agent
            started_at = $script:currentStepStartedAt.ToString("o"); finished_at = $null; seconds = $null
        }) | Out-Null
        Save-PipelineStepTimings -CurrentStep $Key
    } catch {
        Write-Warning "Step timing could not be recorded: $($_.Exception.Message)"
    }
}

function Complete-PipelineSteps {
    try {
        Close-PipelineStep
        Save-PipelineStepTimings -CurrentStep $null
    } catch {
        Write-Warning "Step timing could not be recorded: $($_.Exception.Message)"
    }
}

function Get-TodayLocalTime {
    param([string]$TimeText)
    $parts = $TimeText.Split(":")
    if ($parts.Count -ne 2) {
        throw "Invalid HH:mm time value: $TimeText"
    }
    return [DateTime]::Today.AddHours([int]$parts[0]).AddMinutes([int]$parts[1])
}

function Get-TodayEasternTimeAsLocal {
    param([string]$TimeText)
    $parts = $TimeText.Split(":")
    if ($parts.Count -ne 2) {
        throw "Invalid HH:mm Eastern time value: $TimeText"
    }
    $easternZone = [TimeZoneInfo]::FindSystemTimeZoneById("Eastern Standard Time")
    $easternNow = [TimeZoneInfo]::ConvertTime((Get-Date), $easternZone)
    $easternWallTime = [DateTime]::SpecifyKind(
        [DateTime]::new($easternNow.Year, $easternNow.Month, $easternNow.Day, [int]$parts[0], [int]$parts[1], 0),
        [DateTimeKind]::Unspecified
    )
    $utcTime = [TimeZoneInfo]::ConvertTimeToUtc($easternWallTime, $easternZone)
    return $utcTime.ToLocalTime()
}

$oldBabisPath = Backup-LatestArtifact -SourcePath (Join-Path $outputDir "babis_shortlist_latest.json") -DestinationDir $baselineDir
$oldGiannisPath = Backup-LatestArtifact -SourcePath (Join-Path $outputDir "giannis_rule_based_latest.json") -DestinationDir $baselineDir
$oldYankosPath = Backup-LatestArtifact -SourcePath (Join-Path $outputDir "yankos_strategy_latest.json") -DestinationDir $baselineDir
$oldPeriklisPath = Backup-LatestArtifact -SourcePath (Join-Path $outputDir "periklis_strategy_latest.json") -DestinationDir $baselineDir
$oldPetrosPath = Backup-LatestArtifact -SourcePath (Join-Path $outputDir "petros_review_latest.json") -DestinationDir $baselineDir
$hasComparisonBaseline = ($null -ne $oldBabisPath) -and ($null -ne $oldGiannisPath) -and ($null -ne $oldYankosPath) -and ($null -ne $oldPetrosPath)

if ($FullMarket) {
    # SEC companyfacts for every scanned company, within a nightly budget (owner decision 2026-10-12): Babis may only
    # score SEC-first once the whole universe is covered. A failure here never stops the run.
    Mark-PipelineStep -Key "sec_prefetch" -Agent "babis"
    Write-Host "Prefetching SEC companyfacts for the company universe..."
    $prefetchLog = Join-Path $logDir "companyfacts_prefetch_$timestamp.log"
    & $pythonPath "companyfacts_prefetch.py" 2>&1 | Tee-Object -FilePath $prefetchLog
    if ($LASTEXITCODE -ne 0) {
        Add-PipelineWarning "SEC companyfacts prefetch failed with exit code $LASTEXITCODE. Log: $prefetchLog"
    }
}

Mark-PipelineStep -Key "babis_refresh" -Agent "babis"
if ($FullMarket) {
    $stage1Limit = if ($FullMarketLimit -gt 0) { $FullMarketLimit } else { [int]::MaxValue }
    $universeLabel = if ($FullMarketLimit -gt 0) { "$stage1Limit symbols" } else { "uncapped configured exchanges" }
    Write-Host "Running babis full-market refresh ($universeLabel)..."
    $pipelineArgs = @(
        "us_equity_monitor.py",
        "--config", "scanner_config.json",
        "pipeline",
        "--full-universe",
        "--stage1-limit", "$stage1Limit",
        "--stage2-limit", "0",
        "--stage2-llm-limit", "0",
        "--no-stage1-llm",
        "--no-stage2-llm"
    )
    $watchlistLabel = "full-market"
} else {
    if ($RebuildLock -or -not (Test-Path $LockedWatchlistPath)) {
        $null = Build-LockedWatchlist -DestinationPath $LockedWatchlistPath
    }

    $tickers = Load-LockedWatchlist -Path $LockedWatchlistPath
    $stage1Limit = $tickers.Count

    Write-Host "Running babis daily refresh on locked watchlist ($stage1Limit symbols)..."
    $pipelineArgs = @(
        "us_equity_monitor.py",
        "--config", "scanner_config.json",
        "pipeline",
        "--stage1-limit", "$stage1Limit",
        "--stage2-limit", "0",
        "--stage2-llm-limit", "0",
        "--no-stage1-llm",
        "--no-stage2-llm",
        "--tickers"
    ) + $tickers
    $watchlistLabel = $LockedWatchlistPath
}

$previousErrorAction = $ErrorActionPreference
$ErrorActionPreference = "Continue"
& $pythonPath @pipelineArgs 2>&1 | Tee-Object -FilePath $pipelineLog
$pipelineExitCode = $LASTEXITCODE
$ErrorActionPreference = $previousErrorAction
if ($pipelineExitCode -ne 0) {
    throw "Babis refresh failed with exit code $pipelineExitCode. Log: $pipelineLog"
}

Mark-PipelineStep -Key "giannis_triage" -Agent "giannis"
Write-Host "Running giannis daily non-LLM triage on refreshed shortlist..."
$giannisArgs = @(
    "giannis_rule_based_from_shortlist.py",
    "--shortlist", "output/babis_shortlist_latest.json",
    "--out-prefix", "output/giannis_rule_based"
)

$previousErrorAction = $ErrorActionPreference
$ErrorActionPreference = "Continue"
& $pythonPath @giannisArgs 2>&1 | Tee-Object -FilePath $giannisLog
$giannisExitCode = $LASTEXITCODE
$ErrorActionPreference = $previousErrorAction
if ($giannisExitCode -ne 0) {
    throw "Giannis triage failed with exit code $giannisExitCode. Log: $giannisLog"
}

Mark-PipelineStep -Key "information_ledger" -Agent "babis"
Write-Host "Recording and categorizing information (no evaluation)..."
$informationLog = Join-Path $logDir "dual_watchlist_information_ledger_$timestamp.log"
$informationArgs = @(
    "information_ledger.py",
    "--babis", "output/babis_shortlist_latest.json",
    "--giannis", "output/giannis_rule_based_latest.json",
    "--out", "output/information_ledger_latest.json"
)
& $pythonPath @informationArgs 2>&1 | Tee-Object -FilePath $informationLog
if ($LASTEXITCODE -ne 0) {
    throw "Information ledger recording failed. Log: $informationLog"
}

Mark-PipelineStep -Key "control_plane_batch" -Agent "sector_agents"
Write-Host "Publishing canonical profile -> financial evidence -> fundamental analysis packets..."
$controlPlaneBatchLog = Join-Path $logDir "dual_watchlist_control_plane_batch_$timestamp.log"
$controlPlaneBatchArgs = @(
    "fundamental_control_plane_batch.py",
    "--babis", "output/babis_shortlist_latest.json",
    "--run-id", $ecosystemRunId,
    "--limit", "$FundamentalControlPlaneLimit",
    "--output", "output/fundamental_control_plane_batch_latest.json"
)
$previousErrorAction = $ErrorActionPreference
$ErrorActionPreference = "Continue"
& $pythonPath @controlPlaneBatchArgs 2>&1 | Tee-Object -FilePath $controlPlaneBatchLog
$controlPlaneBatchExitCode = $LASTEXITCODE
$ErrorActionPreference = $previousErrorAction
if ($controlPlaneBatchExitCode -ne 0) {
    Add-PipelineWarning "Control-plane fundamental batch completed with failures. Evidence gaps remain explicit. Log: $controlPlaneBatchLog"
}

Mark-PipelineStep -Key "petros_synthesis" -Agent "petros"
Write-Host "Running Petros fundamental-only synthesis before technical analysis..."
$petrosLog = Join-Path $logDir "dual_watchlist_petros_$timestamp.log"
$petrosArgs = @(
    "petros_agent.py",
    "review",
    "--babis", "output/babis_shortlist_latest.json",
    "--giannis", "output/giannis_rule_based_latest.json",
    "--out-prefix", "output/petros_review",
    "--no-model"
)

$previousErrorAction = $ErrorActionPreference
$ErrorActionPreference = "Continue"
& $pythonPath @petrosArgs 2>&1 | Tee-Object -FilePath $petrosLog
$petrosExitCode = $LASTEXITCODE
$ErrorActionPreference = $previousErrorAction
$petrosReviewOk = $true
if ($petrosExitCode -ne 0) {
    $petrosReviewOk = $false
    throw "Petros fundamental review failed with exit code $petrosExitCode. Trader stages are blocked. Log: $petrosLog"
}

Mark-PipelineStep -Key "deep_research" -Agent "petros"
Write-Host "Running evidence-first deep company research for Petros candidates and manual reviews..."
$deepResearchLog = Join-Path $logDir "dual_watchlist_company_deep_research_$timestamp.log"
& $pythonPath "company_deep_research.py" `
    "--petros" "output/petros_review_latest.json" `
    "--output" "output/company_deep_research_latest.json" `
    "--markdown" "output/company_deep_research_latest.md" 2>&1 | Tee-Object -FilePath $deepResearchLog
if ($LASTEXITCODE -ne 0) {
    throw "Company deep research failed. Fundamental packet creation is blocked. Log: $deepResearchLog"
}

Mark-PipelineStep -Key "freeze_packet" -Agent "petros"
Write-Host "Freezing the Fundamental Research Packet for trader consumption..."
$fundamentalPacketLog = Join-Path $logDir "dual_watchlist_fundamental_packet_$timestamp.log"
$fundamentalPacketArgs = @(
    "fundamental_research_packet.py",
    "--babis", "output/babis_shortlist_latest.json",
    "--giannis", "output/giannis_rule_based_latest.json",
    "--petros", "output/petros_review_latest.json",
    "--deep-research", "output/company_deep_research_latest.json",
    "--out-prefix", "output/fundamental_research_packet"
)
& $pythonPath @fundamentalPacketArgs 2>&1 | Tee-Object -FilePath $fundamentalPacketLog
if ($LASTEXITCODE -ne 0) {
    throw "Fundamental Research Packet build failed. Trader stages are blocked. Log: $fundamentalPacketLog"
}

# Daily prices of the shortlist into the price store, so the traders can score the library strategies
# (owner decision 2026-10-12). Only new sessions are downloaded; a failure never stops the run.
Mark-PipelineStep -Key "price_history" -Agent "system"
Write-Host "Updating stored daily prices for the shortlist..."
$priceLog = Join-Path $logDir "price_history_$timestamp.log"
& $pythonPath "price_history_store.py" "--shortlist" (Join-Path $outputDir "babis_shortlist_latest.json") 2>&1 | Tee-Object -FilePath $priceLog
if ($LASTEXITCODE -ne 0) {
    Add-PipelineWarning "Price history update failed with exit code $LASTEXITCODE. Log: $priceLog"
}

Mark-PipelineStep -Key "yankos" -Agent "yankos"
Write-Host "Running Yankos strategy playbooks..."
$yankosLog = Join-Path $logDir "dual_watchlist_yankos_$timestamp.log"
$yankosArgs = @(
    "yankos_strategy_agent.py",
    "review",
    "--babis", "output/babis_shortlist_latest.json",
    "--giannis", "output/giannis_rule_based_latest.json",
    "--fundamental-packet", "output/fundamental_research_packet_latest.json",
    "--out-prefix", "output/yankos_strategy",
    "--agent-name", "yankos"
)

$previousErrorAction = $ErrorActionPreference
$ErrorActionPreference = "Continue"
& $pythonPath @yankosArgs 2>&1 | Tee-Object -FilePath $yankosLog
$yankosExitCode = $LASTEXITCODE
$ErrorActionPreference = $previousErrorAction
if ($yankosExitCode -ne 0) {
    throw "Yankos strategy review failed with exit code $yankosExitCode. Log: $yankosLog"
}

Mark-PipelineStep -Key "periklis" -Agent "periklis"
Write-Host "Running Periklis swing playbooks..."
$periklisLog = Join-Path $logDir "dual_watchlist_periklis_$timestamp.log"
$periklisArgs = @(
    "periklis_strategy_agent.py",
    "review",
    "--babis", "output/babis_shortlist_latest.json",
    "--giannis", "output/giannis_rule_based_latest.json",
    "--fundamental-packet", "output/fundamental_research_packet_latest.json",
    "--out-prefix", "output/periklis_strategy",
    "--agent-name", "periklis"
)

$previousErrorAction = $ErrorActionPreference
$ErrorActionPreference = "Continue"
& $pythonPath @periklisArgs 2>&1 | Tee-Object -FilePath $periklisLog
$periklisExitCode = $LASTEXITCODE
$ErrorActionPreference = $previousErrorAction
if ($periklisExitCode -ne 0) {
    throw "Periklis strategy review failed with exit code $periklisExitCode. Log: $periklisLog"
}

Mark-PipelineStep -Key "lock_predictions" -Agent "system"
Write-Host "Locking trader predictions before outcomes are evaluated..."
$experimentLog = Join-Path $logDir "dual_watchlist_experiment_registry_$timestamp.log"
$experimentArgs = @(
    "experiment_registry.py",
    "--giannis", "output/giannis_rule_based_latest.json",
    "--strategy", "output/yankos_strategy_latest.json",
    "--strategy", "output/periklis_strategy_latest.json",
    "--horizon-days", "20",
    "--out-prefix", "output/experiment_registry"
)

$previousErrorAction = $ErrorActionPreference
$ErrorActionPreference = "Continue"
& $pythonPath @experimentArgs 2>&1 | Tee-Object -FilePath $experimentLog
$experimentExitCode = $LASTEXITCODE
$ErrorActionPreference = $previousErrorAction
if ($experimentExitCode -ne 0) {
    throw "Immutable experiment registration failed with exit code $experimentExitCode. Log: $experimentLog"
}

Mark-PipelineStep -Key "outcome_baseline" -Agent "system"
Write-Host "Recording outcome-tracking baseline..."
$outcomeLog = Join-Path $logDir "dual_watchlist_outcomes_$timestamp.log"
$outcomeArgs = @(
    "outcome_tracker.py",
    "record",
    "--giannis", "output/giannis_rule_based_latest.json"
)

$previousErrorAction = $ErrorActionPreference
$ErrorActionPreference = "Continue"
& $pythonPath @outcomeArgs 2>&1 | Tee-Object -FilePath $outcomeLog
$outcomeExitCode = $LASTEXITCODE
$ErrorActionPreference = $previousErrorAction
if ($outcomeExitCode -ne 0) {
    Add-PipelineWarning "Outcome baseline recording failed with exit code $outcomeExitCode. Log: $outcomeLog"
}

Mark-PipelineStep -Key "outcome_refresh" -Agent "system"
Write-Host "Refreshing due outcome labels..."
$outcomeRefreshLog = Join-Path $logDir "dual_watchlist_outcome_refresh_$timestamp.log"
$outcomeRefreshArgs = @(
    "outcome_tracker.py",
    "refresh"
)

$previousErrorAction = $ErrorActionPreference
$ErrorActionPreference = "Continue"
& $pythonPath @outcomeRefreshArgs 2>&1 | Tee-Object -FilePath $outcomeRefreshLog
$outcomeRefreshExitCode = $LASTEXITCODE
$ErrorActionPreference = $previousErrorAction
if ($outcomeRefreshExitCode -ne 0) {
    Add-PipelineWarning "Outcome refresh failed with exit code $outcomeRefreshExitCode. Log: $outcomeRefreshLog"
}

Mark-PipelineStep -Key "backtest" -Agent "sokratis"
Write-Host "Building strategy backtest scorecards..."
$backtestLog = Join-Path $logDir "dual_watchlist_strategy_backtest_$timestamp.log"
$backtestArgs = @(
    "strategy_backtest.py",
    "build",
    "--out-prefix", "output/strategy_backtest"
)

$previousErrorAction = $ErrorActionPreference
$ErrorActionPreference = "Continue"
& $pythonPath @backtestArgs 2>&1 | Tee-Object -FilePath $backtestLog
$backtestExitCode = $LASTEXITCODE
$ErrorActionPreference = $previousErrorAction
if ($backtestExitCode -ne 0) {
    Add-PipelineWarning "Strategy backtest build failed with exit code $backtestExitCode. Log: $backtestLog"
}

# Macro environment (economy from FRED, market from the price store), shadow only (owner decision 2026-10-13).
# It first brings every company's stored prices up to date, for breadth. A failure never stops the run.
Mark-PipelineStep -Key "macro_environment" -Agent "system"
Write-Host "Computing the macro environment (shadow)..."
$macroLog = Join-Path $logDir "macro_environment_$timestamp.log"
$previousErrorAction = $ErrorActionPreference
$ErrorActionPreference = "Continue"
& $pythonPath "macro_environment.py" "--update-store" 2>&1 | Tee-Object -FilePath $macroLog
$macroExitCode = $LASTEXITCODE
$ErrorActionPreference = $previousErrorAction
if ($macroExitCode -ne 0) {
    Add-PipelineWarning "Macro environment failed with exit code $macroExitCode. Log: $macroLog"
}

# Results per period (quarters / half-years / years) of every shortlisted company from the stored SEC companyfacts,
# shadow only (owner decision 2026-10-22). A failure never stops the run.
Mark-PipelineStep -Key "periodic_results" -Agent "system"
Write-Host "Building results per period (shadow)..."
$periodicLog = Join-Path $logDir "periodic_results_$timestamp.log"
$previousErrorAction = $ErrorActionPreference
$ErrorActionPreference = "Continue"
& $pythonPath "periodic_results.py" 2>&1 | Tee-Object -FilePath $periodicLog
$periodicExitCode = $LASTEXITCODE
$ErrorActionPreference = $previousErrorAction
if ($periodicExitCode -ne 0) {
    Add-PipelineWarning "Results per period failed with exit code $periodicExitCode. Log: $periodicLog"
}

# Press releases (owner decision 2026-10-22): the company's own results from today's 8-K item 2.02 (exhibit
# 99.1), anchored on the SEC's prior-year quarter; they join the results journal below. A failure never stops the run.
Mark-PipelineStep -Key "press_release_reader" -Agent "system"
Write-Host "Reading new results press releases (shadow)..."
$pressLog = Join-Path $logDir "press_release_reader_$timestamp.log"
$previousErrorAction = $ErrorActionPreference
$ErrorActionPreference = "Continue"
& $pythonPath "press_release_reader.py" 2>&1 | Tee-Object -FilePath $pressLog
$pressExitCode = $LASTEXITCODE
$ErrorActionPreference = $previousErrorAction
if ($pressExitCode -ne 0) {
    Add-PipelineWarning "Press-release reader failed with exit code $pressExitCode. Log: $pressLog"
}

# Results journal (owner decision 2026-10-22): every periodic report of the shortlist, what it said and how the
# market took it; the ecosystem finds and proposes lessons for Giannis by itself (candidates only).
Mark-PipelineStep -Key "earnings_journal" -Agent "system"
Write-Host "Updating the results journal (shadow)..."
$journalLog = Join-Path $logDir "earnings_journal_$timestamp.log"
$previousErrorAction = $ErrorActionPreference
$ErrorActionPreference = "Continue"
& $pythonPath "earnings_journal.py" 2>&1 | Tee-Object -FilePath $journalLog
$journalExitCode = $LASTEXITCODE
$ErrorActionPreference = $previousErrorAction
if ($journalExitCode -ne 0) {
    Add-PipelineWarning "Results journal failed with exit code $journalExitCode. Log: $journalLog"
}

Mark-PipelineStep -Key "sokratis" -Agent "sokratis"
Write-Host "Building Sokratis trader skill lessons..."
$sokratisLog = Join-Path $logDir "dual_watchlist_sokratis_$timestamp.log"
$sokratisArgs = @(
    "sokratis_strategy_teacher.py",
    "build",
    "--backtest", "output/strategy_backtest_latest.json",
    "--yankos", "output/yankos_strategy_latest.json",
    "--periklis", "output/periklis_strategy_latest.json",
    "--out-prefix", "output/sokratis_strategy_teacher"
)

$previousErrorAction = $ErrorActionPreference
$ErrorActionPreference = "Continue"
& $pythonPath @sokratisArgs 2>&1 | Tee-Object -FilePath $sokratisLog
$sokratisExitCode = $LASTEXITCODE
$ErrorActionPreference = $previousErrorAction
if ($sokratisExitCode -ne 0) {
    Add-PipelineWarning "Sokratis skill lesson build failed with exit code $sokratisExitCode. Log: $sokratisLog"
}

Mark-PipelineStep -Key "petros_training" -Agent "petros"
Write-Host "Training Petros safe JSON model when enough labels exist..."
$petrosTrainLog = Join-Path $logDir "dual_watchlist_petros_train_$timestamp.log"
$petrosTrainArgs = @(
    "petros_trainer.py",
    "--horizon-days", "20",
    "train"
)

$previousErrorAction = $ErrorActionPreference
$ErrorActionPreference = "Continue"
& $pythonPath @petrosTrainArgs 2>&1 | Tee-Object -FilePath $petrosTrainLog
$petrosTrainExitCode = $LASTEXITCODE
$ErrorActionPreference = $previousErrorAction
if ($petrosTrainExitCode -ne 0) {
    Add-PipelineWarning "Petros training failed with exit code $petrosTrainExitCode. Log: $petrosTrainLog"
}

$compareLog = Join-Path $logDir "dual_watchlist_compare_$timestamp.log"
if ($hasComparisonBaseline -and $petrosReviewOk) {
    Write-Host "Comparing fresh scan against previous baseline..."
    $compareArgs = @(
        "compare_agent_runs.py",
        "--old-babis", $oldBabisPath,
        "--new-babis", "output/babis_shortlist_latest.json",
        "--old-giannis", $oldGiannisPath,
        "--new-giannis", "output/giannis_rule_based_latest.json",
        "--old-yankos", $oldYankosPath,
        "--new-yankos", "output/yankos_strategy_latest.json",
        "--old-petros", $oldPetrosPath,
        "--new-petros", "output/petros_review_latest.json",
        "--baseline-label", "previous_latest_before_refresh",
        "--current-label", "fresh_scan_with_current_logic",
        "--out-json", $compareJsonPath,
        "--out-md", $compareMdPath
    )
    if (($null -ne $oldPeriklisPath) -and (Test-Path "output/periklis_strategy_latest.json")) {
        $compareArgs += @(
            "--old-periklis", $oldPeriklisPath,
            "--new-periklis", "output/periklis_strategy_latest.json"
        )
    }

    $previousErrorAction = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    & $pythonPath @compareArgs 2>&1 | Tee-Object -FilePath $compareLog
    $compareExitCode = $LASTEXITCODE
    $ErrorActionPreference = $previousErrorAction
    if ($compareExitCode -ne 0) {
        Add-PipelineWarning "Scan comparison failed with exit code $compareExitCode. Log: $compareLog"
    }
} else {
    if (-not $hasComparisonBaseline) {
        "No complete baseline available for compare step." | Set-Content -Path $compareLog -Encoding UTF8
    } else {
        "Compare skipped because Petros review failed in this run." | Set-Content -Path $compareLog -Encoding UTF8
        Add-PipelineWarning "Compare skipped because Petros review failed in this run."
    }
}

Mark-PipelineStep -Key "meeting_report" -Agent "system"
Write-Host "Generating babis x giannis meeting report..."
$meetingLog = Join-Path $logDir "dual_watchlist_meeting_$timestamp.log"
$meetingArgs = @(
    "-ExecutionPolicy", "Bypass",
    "-File", ".\run_babis_giannis_meeting.ps1",
    "-DurationMinutes", "$MeetingDurationMinutes",
    "-BabisFile", ".\output\babis_shortlist_latest.json",
    "-GiannisFile", ".\output\giannis_rule_based_latest.json",
    "-YankosFile", ".\output\yankos_strategy_latest.json",
    "-PeriklisFile", ".\output\periklis_strategy_latest.json",
    "-PetrosFile", ".\output\petros_review_latest.json",
    "-CompareFile", ".\output\scan_compare_latest.json",
    "-OutputDir", ".\output",
    "-LogDir", ".\logs"
)

$previousErrorAction = $ErrorActionPreference
$ErrorActionPreference = "Continue"
& "C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe" @meetingArgs 2>&1 | Tee-Object -FilePath $meetingLog
$meetingExitCode = $LASTEXITCODE
$ErrorActionPreference = $previousErrorAction
if ($meetingExitCode -ne 0) {
    Add-PipelineWarning "Meeting report failed with exit code $meetingExitCode. Log: $meetingLog"
}

Mark-PipelineStep -Key "action_report" -Agent "system"
Write-Host "Generating concise action report..."
$actionLog = Join-Path $logDir "dual_watchlist_action_report_$timestamp.log"
$actionArgs = @(
    "today_action_report.py",
    "--babis", "output/babis_shortlist_latest.json",
    "--giannis", "output/giannis_rule_based_latest.json",
    "--delta", "output/babis_shortlist_delta_latest.json",
    "--yankos", "output/yankos_strategy_latest.json",
    "--periklis", "output/periklis_strategy_latest.json",
    "--strategy-backtest", "output/strategy_backtest_latest.json",
    "--team-decision", "output/team_decision_latest.json",
    "--output", "output/today_action_report.md"
)

$previousErrorAction = $ErrorActionPreference
$ErrorActionPreference = "Continue"
& $pythonPath @actionArgs 2>&1 | Tee-Object -FilePath $actionLog
$actionExitCode = $LASTEXITCODE
$ErrorActionPreference = $previousErrorAction
if ($actionExitCode -ne 0) {
    Add-PipelineWarning "Action report failed with exit code $actionExitCode. Log: $actionLog"
}

Mark-PipelineStep -Key "trader_handoff" -Agent "system"
if ((-not $SubmitPaper) -or $NoAutoTraderStart) {
    $traderHandoffStatus = if ($NoAutoTraderStart) { "disabled_by_legacy_flag" } else { "research_only_default" }
    "Paper submission skipped. Research-only is the safe default; use -SubmitPaper for an explicit paper-execution request." | Set-Content -Path $traderHandoffLog -Encoding UTF8
} else {
    Write-Host "Evaluating automatic trader handoff..."
    $tradingWindowPath = Join-Path $projectRoot "trading_window_config.json"
    $entryFrom = Get-TodayEasternTimeAsLocal -TimeText "09:30"
    $entryUntil = Get-TodayEasternTimeAsLocal -TimeText "15:00"
    if (Test-Path $tradingWindowPath) {
        try {
            $tradingWindow = Get-Content -Path $tradingWindowPath -Raw | ConvertFrom-Json
            if ($null -ne $tradingWindow.entry_allowed_from_eastern) {
                $entryFrom = Get-TodayEasternTimeAsLocal -TimeText ([string]$tradingWindow.entry_allowed_from_eastern)
            } elseif ($null -ne $tradingWindow.entry_allowed_from_athens) {
                $entryFrom = Get-TodayLocalTime -TimeText ([string]$tradingWindow.entry_allowed_from_athens)
            }
            if ($null -ne $tradingWindow.entry_allowed_until_eastern) {
                $entryUntil = Get-TodayEasternTimeAsLocal -TimeText ([string]$tradingWindow.entry_allowed_until_eastern)
            } elseif ($null -ne $tradingWindow.entry_allowed_until_athens) {
                $entryUntil = Get-TodayLocalTime -TimeText ([string]$tradingWindow.entry_allowed_until_athens)
            }
        } catch {
            Add-PipelineWarning "Could not parse trading_window_config.json for trader handoff; using default 09:30-15:00 America/New_York converted to local time. Error: $($_.Exception.Message)"
        }
    }

    $now = Get-Date
    if ($now -lt $entryFrom) {
        $waitSeconds = [int][Math]::Ceiling(($entryFrom - $now).TotalSeconds)
        $traderHandoffStatus = "waiting_until_entry_window"
        "Waiting $waitSeconds second(s) until trader entry window opens at $($entryFrom.ToString("HH:mm"))." | Set-Content -Path $traderHandoffLog -Encoding UTF8
        Write-Host "Trader handoff waiting until $($entryFrom.ToString("HH:mm")) Athens..."
        Start-Sleep -Seconds $waitSeconds
    }

    $now = Get-Date
    if ($now -gt $entryUntil) {
        $traderHandoffStatus = "skipped_after_entry_cutoff"
        $message = "Auto trader handoff skipped because current time $($now.ToString("HH:mm")) is after entry cutoff $($entryUntil.ToString("HH:mm"))."
        $message | Set-Content -Path $traderHandoffLog -Encoding UTF8
        Add-PipelineWarning $message
    } else {
        $traderHandoffStatus = "started"
        $traderArgs = @(
            "-ExecutionPolicy", "Bypass",
            "-File", ".\start_trader_paper_trading_today.ps1",
            "-PythonExe", $pythonPath,
            "-MaxOrders", "$TraderMaxOrders",
            "-MaxShortOrders", "$TraderMaxShortOrders",
            "-MinScore", "$TraderMinScore",
            "-NotionalPerTrade", "$TraderNotionalPerTrade",
            "-NotionalSource", "$TraderNotionalSource",
            "-TakeProfitPct", "$TraderTakeProfitPct",
            "-TakeProfitSource", "$TraderTakeProfitSource",
            "-StopLossPct", "$TraderStopLossPct",
            "-StopLossSource", "$TraderStopLossSource",
            "-HoldingPeriodRule", "$TraderHoldingPeriodRule",
            "-HoldingPeriodSource", "$TraderHoldingPeriodSource",
            "-EntryTimeframe", "$TraderEntryTimeframe",
            "-MiltosPollSeconds", "$MiltosPollSeconds",
            "-MiltosMaxCycles", "$MiltosMaxCycles"
        )

        $previousErrorAction = $ErrorActionPreference
        $ErrorActionPreference = "Continue"
        & "C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe" @traderArgs 2>&1 | Tee-Object -FilePath $traderHandoffLog
        $traderHandoffExitCode = $LASTEXITCODE
        $ErrorActionPreference = $previousErrorAction
        if ($traderHandoffExitCode -ne 0) {
            $traderHandoffStatus = "failed"
            Add-PipelineWarning "Auto trader handoff failed with exit code $traderHandoffExitCode. Log: $traderHandoffLog"
        } else {
            $traderHandoffStatus = "completed"
        }
    }
}

$summaryPath = Join-Path $outputDir "dual_watchlist_summary_$timestamp.md"
$babisLatestPath = Join-Path $outputDir "babis_shortlist_latest.json"
$actualSymbolCount = $stage1Limit
$actualShortlistCount = $null
if (Test-Path $babisLatestPath) {
    try {
        $babisLatest = Get-Content $babisLatestPath -Raw | ConvertFrom-Json
        if ($null -ne $babisLatest.universe_total) {
            $actualSymbolCount = [int]$babisLatest.universe_total
        }
        if ($null -ne $babisLatest.shortlist_total) {
            $actualShortlistCount = [int]$babisLatest.shortlist_total
        }
    } catch {
        $actualSymbolCount = $stage1Limit
    }
}
$actualShortlistLabel = if ($null -ne $actualShortlistCount) { "$actualShortlistCount" } else { "unknown" }
$warningCount = $pipelineWarnings.Count
$warningLabel = if ($warningCount -eq 0) { "none" } else { "$warningCount warning(s)" }
$summaryLines = @(
    "# Daily Dual Watchlist Summary",
    "",
    "- Run timestamp: $(Get-Date -Format o)",
    "- Run ID: $ecosystemRunId",
    "- Watchlist mode: $watchlistLabel",
    "- Symbol count: $actualSymbolCount",
    "- Shortlist count: $actualShortlistLabel",
    "- Pipeline warnings: $warningLabel",
    "- Babis latest: output/babis_shortlist_latest.json",
    "- Babis delta latest: output/babis_shortlist_delta_latest.json",
    "- Pipeline runtime latest: output/pipeline_runtime_latest.md",
    "- Scan compare latest: output/scan_compare_latest.md",
    "- Giannis latest (MD): output/giannis_rule_based_latest.md",
    "- Giannis latest (JSON): output/giannis_rule_based_latest.json",
    "- Yankos latest: output/yankos_strategy_latest.md",
    "- Periklis latest: output/periklis_strategy_latest.md",
    "- Strategy backtest latest: output/strategy_backtest_latest.md",
    "- Sokratis strategy teacher latest: output/sokratis_strategy_teacher_latest.md",
    "- Petros latest: output/petros_review_latest.md",
    "- Company deep research: output/company_deep_research_latest.md",
    "- Fundamental Research Packet: output/fundamental_research_packet_latest.md",
    "- Canonical control-plane batch: output/fundamental_control_plane_batch_latest.json",
    "- Team decision latest: output/team_decision_latest.md",
    "- Petros training report: output/petros_training_latest.md",
    "- Petros model: models/petros_model_latest.json",
    "- Trader handoff status: $traderHandoffStatus",
    "- Trader handoff report: $traderHandoffReport",
    "- Action report: output/today_action_report.md",
    "- Outcome tracking: data/market_intel.db",
    "- Pipeline log: $pipelineLog",
    "- Giannis log: $giannisLog",
    "- Yankos log: $yankosLog",
    "- Periklis log: $periklisLog",
    "- Petros log: $petrosLog",
    "- Company deep research log: $deepResearchLog",
    "- Fundamental packet log: $fundamentalPacketLog",
    "- Control-plane batch log: $controlPlaneBatchLog",
    "- Outcome log: $outcomeLog",
    "- Outcome refresh log: $outcomeRefreshLog",
    "- Strategy backtest log: $backtestLog",
    "- Sokratis log: $sokratisLog",
    "- Petros training log: $petrosTrainLog",
    "- Compare log: $compareLog",
    "- Meeting log: $meetingLog",
    "- Action log: $actionLog",
    "- Trader handoff log: $traderHandoffLog"
)
if ($pipelineWarnings.Count -gt 0) {
    $summaryLines += ""
    $summaryLines += "## Pipeline Warnings"
    $summaryLines += ""
    foreach ($warning in $pipelineWarnings) {
        $summaryLines += "- $warning"
    }
}
$summaryLines | Set-Content -Path $summaryPath -Encoding UTF8

$finishedPath = Join-Path $outputDir "RUN_FINISHED_READ_ME.txt"
$finishedLines = @(
    "Babis + Giannis + Yankos + Periklis + Petros run completed.",
    "Completed at: $(Get-Date -Format o)",
    "Mode: $watchlistLabel",
    "Warnings: $warningLabel",
    "Summary: $summaryPath",
    "Babis latest: output/babis_shortlist_latest.json",
    "Scan compare latest: output/scan_compare_latest.md",
    "Giannis latest: output/giannis_rule_based_latest.md",
    "Yankos latest: output/yankos_strategy_latest.md",
    "Periklis latest: output/periklis_strategy_latest.md",
    "Strategy backtest latest: output/strategy_backtest_latest.md",
    "Sokratis latest: output/sokratis_strategy_teacher_latest.md",
    "Petros latest: output/petros_review_latest.md",
    "Company deep research: output/company_deep_research_latest.md",
    "Fundamental Research Packet: output/fundamental_research_packet_latest.md",
    "Canonical control-plane batch: output/fundamental_control_plane_batch_latest.json",
    "Team decision latest: output/team_decision_latest.md",
    "Petros training: output/petros_training_latest.md",
    "Petros model: models/petros_model_latest.json",
    "Trader handoff status: $traderHandoffStatus",
    "Trader handoff report: $traderHandoffReport",
    "Trader handoff log: $traderHandoffLog",
    "Meeting report: check latest output/meeting_team_daily_*.md",
    "Action report: output/today_action_report.md",
    "Outcome tracking DB: data/market_intel.db"
)
if ($pipelineWarnings.Count -gt 0) {
    $finishedLines += ""
    $finishedLines += "Warnings:"
    foreach ($warning in $pipelineWarnings) {
        $finishedLines += "- $warning"
    }
}
$finishedLines | Set-Content -Path $finishedPath -Encoding UTF8

Mark-PipelineStep -Key "coordination_audit" -Agent "system"
$coordinationOutboxRelative = "output/integration_outbox/${ecosystemRunId}_daily_research_digest.json"
$coordinationArgs = @(
    "ecosystem_coordination.py",
    "--run-id", $ecosystemRunId,
    "--generated-at", (Get-Date -Format o),
    "--trader-handoff-status", $traderHandoffStatus,
    "--summary-path", $summaryPath
)
if ($SubmitPaper) {
    $coordinationArgs += "--paper-requested"
}
foreach ($warning in $pipelineWarnings) {
    $coordinationArgs += "--warning"
    $coordinationArgs += $warning
}
try {
    & $pythonPath @coordinationArgs
    if ($LASTEXITCODE -ne 0) {
        Add-PipelineWarning "Coordination outbox generation returned exit code $LASTEXITCODE."
    }
} catch {
    Add-PipelineWarning "Coordination outbox generation failed: $($_.Exception.Message)"
}

if ($FullMarket -and $NoAutoTraderStart) {
    & $pythonPath "agent_run_audit.py" "--phase" "research_only" "--run-id" $ecosystemRunId
    if ($LASTEXITCODE -ne 0) {
        Add-PipelineWarning "Research-only agent audit failed to run."
    } else {
        $agentAudit = Get-Content "output/research_only_audit_latest.json" -Raw | ConvertFrom-Json
        if ($agentAudit.run_id -ne $ecosystemRunId -or $agentAudit.status -ne "pass") {
            Add-PipelineWarning "Research-only agent audit requires review."
        }
    }
}

Complete-PipelineSteps
$manifestArgs = @(
    "run_manifest.py",
    "--run-id", $ecosystemRunId,
    "--config", "scanner_config.json",
    "--config", "universe_eligibility_overrides.json",
    "--config", "trading_window_config.json",
    "--artifact", "output/babis_shortlist_latest.json",
    "--artifact", "output/pipeline_step_timings_latest.json",
    "--artifact", "output/universe_eligibility_latest.json",
    "--artifact", "output/all_listed_universe_routes_latest.json",
    "--artifact", "output/all_listed_instrument_observations_latest.json",
    "--artifact", "output/giannis_rule_based_latest.json",
    "--artifact", "output/company_deep_research_latest.json",
    "--artifact", "output/information_ledger_latest.json",
    "--artifact", "output/fundamental_research_packet_latest.json",
    "--artifact", "output/fundamental_control_plane_batch_latest.json",
    "--artifact", "output/yankos_strategy_latest.json",
    "--artifact", "output/periklis_strategy_latest.json",
    "--artifact", "output/experiment_registry_latest.json",
    "--artifact", "output/strategy_backtest_latest.json",
    "--artifact", "output/macro_environment_latest.json",
    "--artifact", "output/periodic_results_latest.json",
    "--artifact", "output/earnings_journal_latest.json",
    "--artifact", "output/paper_learning/evaluation_latest.json",
    "--artifact", "output/sokratis_strategy_teacher_latest.json",
    "--artifact", "output/petros_review_latest.json",
    "--artifact", "output/petros_training_latest.md",
    "--artifact", "output/team_decision_latest.json",
    "--artifact", "output/today_action_report.md",
    "--artifact", $summaryPath,
    "--artifact", $traderHandoffLog
)
if ($FullMarket -and $NoAutoTraderStart -and (Test-Path "output/research_only_audit_latest.json")) {
    $auditForManifest = Get-Content "output/research_only_audit_latest.json" -Raw | ConvertFrom-Json
    if ($auditForManifest.run_id -eq $ecosystemRunId) {
        $manifestArgs += "--artifact"
        $manifestArgs += "output/research_only_audit_latest.json"
    }
}
if (Test-Path $coordinationOutboxRelative) {
    $manifestArgs += "--artifact"
    $manifestArgs += $coordinationOutboxRelative
}
if ($traderHandoffStatus -eq "completed") {
    $manifestArgs += "--artifact"
    $manifestArgs += "output/koulis_order_intents_latest.json"
    $manifestArgs += "--artifact"
    $manifestArgs += "output/trader_paper_trading_run_latest.json"
}
try {
    & $pythonPath @manifestArgs
    if ($LASTEXITCODE -ne 0) {
        Add-PipelineWarning "Run manifest capture returned exit code $LASTEXITCODE."
    }
} catch {
    Add-PipelineWarning "Run manifest capture failed: $($_.Exception.Message)"
}

if (-not $NoCompletionPopup) {
    try {
        [console]::beep(880, 500)
        Start-Sleep -Milliseconds 120
        [console]::beep(988, 500)
        Start-Sleep -Milliseconds 120
        [console]::beep(1175, 800)
    } catch {
        try {
            Add-Type -AssemblyName System.Windows.Forms
            [System.Media.SystemSounds]::Asterisk.Play()
        } catch {
            Write-Host "Audio notification unavailable: $($_.Exception.Message)"
        }
    }

    try {
        Add-Type -AssemblyName System.Windows.Forms
        [System.Windows.Forms.MessageBox]::Show(
            "Babis + Giannis + Yankos + Periklis + Petros finished. Meeting report and decision review are ready.",
            "Market scan completed",
            [System.Windows.Forms.MessageBoxButtons]::OK,
            [System.Windows.Forms.MessageBoxIcon]::Information
        ) | Out-Null
    } catch {
        Write-Host "Popup notification unavailable: $($_.Exception.Message)"
    }
}

Write-Host "Daily dual-watchlist run completed."
Write-Host "Summary: $summaryPath"
Write-Host "Finished marker: $finishedPath"
