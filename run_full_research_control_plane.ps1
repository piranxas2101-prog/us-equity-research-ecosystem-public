param(
    [int]$UniverseLimit = 0,
    [int]$FundamentalControlPlaneLimit = 0,
    [string]$CompletionReportPath = ""
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $projectRoot
$outputDir = Join-Path $projectRoot "output"
$logDir = Join-Path $projectRoot "logs"
New-Item -ItemType Directory -Path $outputDir -Force | Out-Null
New-Item -ItemType Directory -Path $logDir -Force | Out-Null
$pointer = Join-Path $outputDir "full_research_control_plane_background_latest.json"
$timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
$runId = "full_research_$timestamp"
$pythonPath = Join-Path $projectRoot ".venv-next\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $pythonPath)) {
    throw "The locked .venv-next Python runtime is required for a full research run."
}

$state = [ordered]@{
    schema = "ecosystem.full_research_control_plane_background.v1"
    run_id = $runId
    started_at = (Get-Date -Format o)
    pid = $PID
    universe_limit = $UniverseLimit
    fundamental_control_plane_limit = $FundamentalControlPlaneLimit
    research_only = $true
    paper_submission_requested = $false
    live_money = $false
    python_runtime = $pythonPath
    status = "running_pipeline"
}
$state | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $pointer -Encoding UTF8

# Keep the computer from going to sleep BY ITSELF while the run lasts (ES_CONTINUOUS | ES_SYSTEM_REQUIRED).
# This does not stop sleep that a person asks for (lid, power button, Start > Sleep) and changes no Windows setting;
# the request ends with this process.
try {
    if (-not ("Win32.PowerState" -as [type])) {
        Add-Type -Namespace Win32 -Name PowerState -MemberDefinition '[DllImport("kernel32.dll")] public static extern uint SetThreadExecutionState(uint esFlags);'
    }
    [void][Win32.PowerState]::SetThreadExecutionState(0x80000001)
} catch {
    Write-Warning "Could not request keep-awake: $($_.Exception.Message)"
}

function Write-CompletionReport {
    param([System.Collections.IDictionary]$RunState, [string]$Path)
    if ([string]::IsNullOrWhiteSpace($Path)) { return }
    $parent = Split-Path -Parent $Path
    if ($parent) { New-Item -ItemType Directory -Path $parent -Force | Out-Null }
    $lines = @(
        "# Full-market research run",
        "",
        "- Run ID: $($RunState.run_id)",
        "- Status: $($RunState.status)",
        "- Started: $($RunState.started_at)",
        "- Finished: $($RunState.finished_at)",
        "- Python runtime: $($RunState.python_runtime)",
        "- Listed-symbol universe: $($RunState.universe_total)",
        "- Company fundamental route: $($RunState.company_fundamental_symbols)",
        "- Instrument observation routes: $($RunState.instrument_observation_symbols)",
        "- Processed: $($RunState.processed_symbols)",
        "- Resumed from checkpoint: $($RunState.resumed_from)",
        "- Technical errors: $($RunState.technical_errors)",
        "- Receipt status: $($RunState.receipt_status)",
        "- Agent audit status: $($RunState.agent_audit_status)",
        "- Agent learning status: $($RunState.learning_status)",
        "- Fundamental learning evaluation: $($RunState.learning_evaluation_status)",
        "- Research only: $($RunState.research_only); paper submission: $($RunState.paper_submission_requested); live money: $($RunState.live_money)",
        "- Error: $($RunState.error)"
    )
    $lines | Set-Content -LiteralPath $Path -Encoding UTF8
}

Write-CompletionReport -RunState $state -Path $CompletionReportPath

try {
    $dailyArgs = @{
        RunId = $runId
        FullMarket = $true
        FullMarketLimit = $UniverseLimit
        FundamentalControlPlaneLimit = $FundamentalControlPlaneLimit
        MeetingDurationMinutes = 0
        NoAutoTraderStart = $true
        NoCompletionPopup = $true
        PythonPath = $pythonPath
    }
    & ".\run_daily_dual_watchlist.ps1" @dailyArgs
    if ($LASTEXITCODE -ne 0) { throw "Full research pipeline failed with exit code $LASTEXITCODE" }

    $babis = Get-Content "output/babis_shortlist_latest.json" -Raw | ConvertFrom-Json
    $scannerConfig = Get-Content "scanner_config.json" -Raw | ConvertFrom-Json
    $configuredCap = [int]$scannerConfig.universe.max_symbols
    $coverage = Get-Content "data/research_universe_exports/coverage.json" -Raw | ConvertFrom-Json
    $catalogTotal = [int]$coverage.unique_symbols
    $state.universe_total = [int]$babis.universe_total
    $state.catalog_total = $catalogTotal
    $state.company_fundamental_symbols = [int]$babis.universe_routing.company_fundamental_symbols
    $state.instrument_observation_symbols = [int]$babis.universe_routing.instrument_observation_symbols
    $state.processed_symbols = [int]$babis.runtime_summary.processed_symbols
    $state.resumed_from = [int]$babis.checkpoint_meta.resumed_from
    $state.technical_errors = [int]$babis.data_quality_summary.technical_error_count
    if ($UniverseLimit -eq 0 -and ($babis.scan_scope -ne "best-effort full universe" -or $state.universe_total -le $configuredCap)) {
        throw "Full-universe guard failed: scan scope or count still reflects the daily cap."
    }
    if ($UniverseLimit -eq 0 -and $state.universe_total -ne $catalogTotal) {
        throw "All-listed guard failed: resolved $($state.universe_total) of $catalogTotal catalog symbols."
    }
    if ($UniverseLimit -eq 0 -and [int]$babis.universe_routing.assigned_symbols -ne $catalogTotal) {
        throw "Routing guard failed: not every catalog symbol received a research route."
    }
    if ($state.processed_symbols -ne $state.universe_total) {
        throw "Incomplete scan: processed $($state.processed_symbols) of $($state.universe_total)."
    }

    $state.status = "building_receipt"
    $state | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $pointer -Encoding UTF8
    & $pythonPath "research_run_receipt.py" `
        "--run-id" $runId `
        "--json" "output/research_run_receipt_latest.json" `
        "--markdown" "output/research_run_receipt_latest.md"
    if ($LASTEXITCODE -ne 0) { throw "Research run receipt failed with exit code $LASTEXITCODE" }

    $receipt = Get-Content "output/research_run_receipt_latest.json" -Raw | ConvertFrom-Json
    $state.receipt_status = [string]$receipt.status
    $agentAudit = Get-Content "output/research_only_audit_latest.json" -Raw | ConvertFrom-Json
    $state.agent_audit_status = if ($agentAudit.run_id -eq $runId) { [string]$agentAudit.status } else { "run_id_mismatch" }

    $state.status = "recording_learning_episodes"
    $state | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $pointer -Encoding UTF8
    & $pythonPath "record_agent_learning_run.py" "--run-id" $runId
    if ($LASTEXITCODE -ne 0) { throw "Agent learning episode recording failed" }
    & $pythonPath "fundamental_learning_evaluator.py" `
        "--run-id" $runId `
        "--output" "output/fundamental_learning_evaluation_latest.json"
    if ($LASTEXITCODE -ne 0) { throw "Independent fundamental learning evaluation failed" }
    $learningEvaluation = Get-Content "output/fundamental_learning_evaluation_latest.json" -Raw | ConvertFrom-Json
    if ($learningEvaluation.run_id -ne $runId -or $learningEvaluation.sokratis_role_changed -or $learningEvaluation.active_decisions_changed) {
        throw "Independent learning evaluation contract failed"
    }
    $state["learning_evaluation_status"] = "completed"
    $state["learning_evaluation_count"] = [int]$learningEvaluation.evaluation_count
    # Read-only checks of realised outcomes: Giannis tiers and Petros verdicts against SPY, trader confidence
    # labels against their results. Reports and lesson candidates only; a failure here never fails the run.
    try {
        & $pythonPath "outcome_evaluator.py" "--out-prefix" "output/outcome_evaluation"
        if ($LASTEXITCODE -ne 0) { Write-Warning "Outcome evaluation failed with exit code $LASTEXITCODE" }
        & $pythonPath "trader_calibration.py" "--out-prefix" "output/trader_calibration"
        if ($LASTEXITCODE -ne 0) { Write-Warning "Trader calibration failed with exit code $LASTEXITCODE" }
    } catch {
        Write-Warning "Outcome checks could not run: $($_.Exception.Message)"
    }
    & $pythonPath "agent_learning_control_plane.py" "dashboard"
    if ($LASTEXITCODE -ne 0) { throw "Agent learning dashboard generation failed" }
    & $pythonPath "analyst_report.py"
    if ($LASTEXITCODE -ne 0) { throw "Analyst report generation failed" }
    & $pythonPath "research_portal.py"
    if ($LASTEXITCODE -ne 0) { throw "Research portal generation failed" }
    $state.learning_status = "episodes_recorded"
    $state.status = if ($receipt.status -eq "ready" -and $state.technical_errors -eq 0 -and $state.agent_audit_status -eq "pass") { "completed" } else { "completed_review_required" }
} catch {
    $state.status = "failed"
    $state.error = $_.Exception.Message
} finally {
    try { [void][Win32.PowerState]::SetThreadExecutionState(0x80000000) } catch { }
    $state.finished_at = (Get-Date -Format o)
    $state.batch_report = "output\fundamental_control_plane_batch_latest.json"
    $state.receipt_json = "output\research_run_receipt_latest.json"
    $state.receipt_markdown = "output\research_run_receipt_latest.md"
    $state | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $pointer -Encoding UTF8
    Write-CompletionReport -RunState $state -Path $CompletionReportPath
    # Daily report of what each agent did and why (also for a failed run: it shows
    # the step it stopped in). Never changes the run's outcome.
    try {
        & $pythonPath "daily_agent_report.py" "--run-id" $runId
    } catch {
        Write-Warning "Daily agent report could not be written: $($_.Exception.Message)"
    }
    # One row per run in output/run_performance_history.csv (durations per step, counts, gaps, changes).
    try {
        & $pythonPath "run_performance_log.py" "record" "--run-id" $runId
    } catch {
        Write-Warning "Run performance row could not be written: $($_.Exception.Message)"
    }
    & ".\notify_full_research_control_plane.ps1" -StatePath $pointer
}

if ($state.status -eq "failed") { exit 1 }
exit 0
