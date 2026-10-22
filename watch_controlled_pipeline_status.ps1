param(
    [int]$IntervalSeconds = 10,
    [int]$TailLines = 30
)

$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$pointerPath = Join-Path $projectRoot "output\controlled_pipeline_latest_background.json"
$checkpointPath = Join-Path $projectRoot "output\babis_stage1_checkpoint.json"

function Read-JsonFile {
    param([string]$Path)
    if (-not (Test-Path -LiteralPath $Path)) {
        return $null
    }
    try {
        return Get-Content -LiteralPath $Path -Raw | ConvertFrom-Json
    } catch {
        return $null
    }
}

function Get-StageLabel {
    param(
        [object]$State,
        [object]$Checkpoint
    )
    $outputDir = Join-Path $projectRoot "output"
    $hasBabis = Test-Path -LiteralPath (Join-Path $outputDir "babis_shortlist_latest.json")
    $hasGiannis = Test-Path -LiteralPath (Join-Path $outputDir "giannis_rule_based_latest.json")
    $hasYankos = Test-Path -LiteralPath (Join-Path $outputDir "yankos_strategy_latest.json")
    $hasPetros = Test-Path -LiteralPath (Join-Path $outputDir "petros_review_latest.json")

    if ($hasPetros) { return "Petros synthesis / review" }
    if ($hasYankos) { return "Trader strategy review" }
    if ($hasGiannis) { return "Giannis quality/risk triage" }
    if ($hasBabis) { return "Babis shortlist complete / handoff starting" }
    if ($null -ne $Checkpoint) { return "Babis Stage 1 market scan" }
    return "Starting / waiting for first checkpoint"
}

while ($true) {
    Clear-Host
    Write-Host "Trading ecosystem live monitor"
    Write-Host ("Time: {0}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"))
    Write-Host ""

    $state = Read-JsonFile -Path $pointerPath
    if ($null -eq $state) {
        Write-Host "No controlled pipeline pointer found:"
        Write-Host $pointerPath
        Start-Sleep -Seconds $IntervalSeconds
        continue
    }

    $pidValue = [int]$state.pid
    $proc = Get-Process -Id $pidValue -ErrorAction SilentlyContinue
    $running = $null -ne $proc
    $checkpoint = Read-JsonFile -Path $checkpointPath
    $stage = Get-StageLabel -State $state -Checkpoint $checkpoint

    Write-Host "Run"
    Write-Host ("  Started: {0}" -f $state.started_at)
    Write-Host ("  PID: {0}" -f $pidValue)
    Write-Host ("  Running: {0}" -f $running)
    Write-Host ("  Stage: {0}" -f $stage)
    Write-Host ("  Config: {0}" -f $state.config)
    Write-Host ("  LLM: {0}" -f $state.use_llm)
    Write-Host ("  Broker actions: {0}" -f $state.broker_actions)
    Write-Host ""

    if ($null -ne $checkpoint) {
        $processed = [int]($checkpoint.processed_count)
        $total = [int]($checkpoint.symbols_total)
        $pct = if ($total -gt 0) { [math]::Round(($processed / $total) * 100, 1) } else { 0 }
        $runtime = $checkpoint.runtime_summary

        Write-Host "Babis Stage 1 progress"
        Write-Host ("  Processed: {0} / {1} ({2}%)" -f $processed, $total, $pct)
        Write-Host ("  OK rows: {0}" -f $checkpoint.success_count)
        Write-Host ("  Errors: {0}" -f $checkpoint.error_count)
        if ($checkpoint.excluded_counts) {
            Write-Host ("  Skipped by price: {0}" -f $checkpoint.excluded_counts.price_above_max)
        }
        if ($runtime) {
            Write-Host ("  Elapsed: {0}s" -f $runtime.elapsed_seconds)
            Write-Host ("  Avg per symbol: {0}s" -f $runtime.avg_snapshot_seconds)
            Write-Host ("  Market data time: {0}s" -f $runtime.market_data_seconds)
            Write-Host ("  SEC time: {0}s" -f $runtime.sec_seconds)
        }
        Write-Host ""
    }

    Write-Host "Recent outputs"
    $latestOutputs = Get-ChildItem -Path (Join-Path $projectRoot "output") -ErrorAction SilentlyContinue |
        Sort-Object LastWriteTime -Descending |
        Select-Object -First 8 Name, Length, LastWriteTime
    if ($latestOutputs) {
        $latestOutputs | Format-Table -AutoSize
    } else {
        Write-Host "  No output files yet."
    }

    Write-Host ""
    Write-Host "Last output log lines"
    if (Test-Path -LiteralPath $state.out_log) {
        Get-Content -LiteralPath $state.out_log -Tail $TailLines
    } else {
        Write-Host "  Missing output log."
    }

    if (Test-Path -LiteralPath $state.err_log) {
        $errItem = Get-Item -LiteralPath $state.err_log
        if ($errItem.Length -gt 0) {
            Write-Host ""
            Write-Host "Last error/warning lines"
            Get-Content -LiteralPath $state.err_log -Tail 12
        }
    }

    Write-Host ""
    Write-Host ("Refreshing every {0}s. Press Ctrl+C to close this monitor; the scan keeps running." -f $IntervalSeconds)
    Start-Sleep -Seconds $IntervalSeconds
}
