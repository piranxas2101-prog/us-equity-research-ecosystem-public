param([int]$RefreshSeconds = 5)

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$pointerPath = Join-Path $projectRoot "output\full_ecosystem_test_latest.json"

function Get-Stage([string]$text, [string]$state) {
    if ($state -eq "pipeline_failed") { return "FAILED" }
    if ($state -eq "pipeline_completed") { return "PIPELINE COMPLETED / MONITORS MAY CONTINUE" }
    $rules = @(
        @{ Pattern = "Running babis|Babis refresh"; Stage = "1/12 BABIS — market/fundamental discovery" },
        @{ Pattern = "Running giannis|Giannis triage"; Stage = "2/12 GIANNIS — quality and risk triage" },
        @{ Pattern = "Recording and categorizing information"; Stage = "3/12 INFORMATION LEDGER — recording/classification" },
        @{ Pattern = "Running Yankos"; Stage = "4/12 YANKOS — independent reasoning" },
        @{ Pattern = "Running Periklis"; Stage = "5/12 PERIKLIS — independent reasoning" },
        @{ Pattern = "Locking trader predictions"; Stage = "6/12 IMMUTABLE EXPERIMENT REGISTRY" },
        @{ Pattern = "Recording outcome|Refreshing due outcome"; Stage = "7/12 OUTCOME TRACKING" },
        @{ Pattern = "Building strategy backtest"; Stage = "8/12 TRADE SCORECARD REFRESH" },
        @{ Pattern = "Building Sokratis|Sokratis"; Stage = "9/12 SOKRATIS — lessons/review" },
        @{ Pattern = "Training Petros|Running Petros|Petros"; Stage = "10/12 PETROS — synthesis/memory" },
        @{ Pattern = "Trader handoff waiting|waiting until"; Stage = "11/12 WAITING FOR MARKET ENTRY WINDOW" },
        @{ Pattern = "Evaluating automatic trader handoff|PREFLIGHT|KOULIS|THYMIOS|paper trader"; Stage = "11/12 KOULIS/THYMIOS/PAPER HANDOFF" },
        @{ Pattern = "Starting position monitors|MONITORS|Position Monitor"; Stage = "12/12 POSITION MONITORING" },
        @{ Pattern = "Daily dual-watchlist run completed"; Stage = "PIPELINE COMPLETED / POSITION MONITORS" }
    )
    $found = "STARTING"
    foreach ($rule in $rules) {
        if ($text -match $rule.Pattern) { $found = $rule.Stage }
    }
    return $found
}

while ($true) {
    Clear-Host
    Write-Host "FULL ECOSYSTEM TEST — LIVE STATUS" -ForegroundColor Cyan
    Write-Host (Get-Date -Format "yyyy-MM-dd HH:mm:ss zzz")
    Write-Host ""
    if (-not (Test-Path $pointerPath)) {
        Write-Host "Waiting for background pointer..." -ForegroundColor Yellow
        Start-Sleep -Seconds $RefreshSeconds
        continue
    }
    try {
        $state = Get-Content $pointerPath -Raw | ConvertFrom-Json
        $transcriptText = ""
        $tail = @()
        if ($state.transcript -and (Test-Path $state.transcript)) {
            $tail = @(Get-Content $state.transcript -Tail 35)
            $transcriptText = $tail -join "`n"
        }
        $process = Get-Process -Id ([int]$state.pid) -ErrorAction SilentlyContinue
        $stage = Get-Stage -text $transcriptText -state ([string]$state.status)
        Write-Host "Stage: $stage" -ForegroundColor Green
        Write-Host "Mode: $($state.mode)"
        Write-Host "Main PID: $($state.pid) | Running: $([bool]$process)"
        Write-Host "Universe: $($state.universe_limit) | Paper only: $(-not [bool]$state.live_money) | Max orders: $($state.max_orders) | Max shorts: $($state.max_short_orders)"
        Write-Host "Started: $($state.started_at)"
        Write-Host ""
        Write-Host "Recent activity:" -ForegroundColor Cyan
        $tail | ForEach-Object { Write-Host $_ }
        if ($state.status -in @("pipeline_completed", "pipeline_failed")) {
            Write-Host ""
            Write-Host "Main pipeline ended. Position-monitor and EOD artifacts remain under output/." -ForegroundColor Yellow
        }
    } catch {
        Write-Host "Status read error: $($_.Exception.Message)" -ForegroundColor Red
    }
    Start-Sleep -Seconds ([Math]::Max(2, $RefreshSeconds))
}
