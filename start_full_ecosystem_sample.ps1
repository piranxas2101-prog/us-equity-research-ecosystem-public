param(
    [int]$UniverseLimit = 1200,
    [int]$MaxOrders = 1000,
    [int]$MaxShortOrders = 1000,
    [double]$MinScore = 0.0,
    [double]$NotionalPerTrade = 250.0
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $projectRoot
$timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
$transcript = Join-Path $projectRoot "logs\full_ecosystem_sample_$timestamp.transcript.log"
$pointer = Join-Path $projectRoot "output\full_ecosystem_test_latest.json"

$state = [ordered]@{
    schema = "ecosystem.full_test_background.v1"
    started_at = (Get-Date -Format o)
    pid = $PID
    mode = "broad_paper_sampling_full_market_$($UniverseLimit)_symbols"
    universe_limit = $UniverseLimit
    paper_submission_requested = $true
    live_money = $false
    max_orders = $MaxOrders
    max_short_orders = $MaxShortOrders
    min_score = $MinScore
    notional_per_trade = $NotionalPerTrade
    transcript = $transcript
    status = "running"
}
$state | ConvertTo-Json -Depth 5 | Set-Content -Path $pointer -Encoding UTF8

Start-Transcript -Path $transcript -Force | Out-Null
try {
    Write-Host "FULL ECOSYSTEM PAPER-SAMPLING TEST"
    Write-Host "Mode: PAPER ONLY | Universe=$UniverseLimit | MaxOrders=$MaxOrders | MaxShortOrders=$MaxShortOrders | MinScore=$MinScore"
    Write-Host "The terminal may remain open while the ecosystem and position monitors work."
    & ".\run_daily_dual_watchlist.ps1" `
        -FullMarket `
        -FullMarketLimit $UniverseLimit `
        -SubmitPaper `
        -TraderMaxOrders $MaxOrders `
        -TraderMaxShortOrders $MaxShortOrders `
        -TraderMinScore $MinScore `
        -TraderNotionalPerTrade $NotionalPerTrade `
        -TraderNotionalSource "agent_suggestion" `
        -TraderTakeProfitSource "agent_suggestion" `
        -TraderStopLossSource "agent_suggestion" `
        -TraderHoldingPeriodSource "agent_suggestion"
    $state.status = if ($LASTEXITCODE -eq 0) { "pipeline_completed" } else { "pipeline_failed" }
    $state.exit_code = $LASTEXITCODE
} catch {
    $state.status = "pipeline_failed"
    $state.error = $_.Exception.Message
    Write-Error $_
} finally {
    $state.finished_at = (Get-Date -Format o)
    $state | ConvertTo-Json -Depth 5 | Set-Content -Path $pointer -Encoding UTF8
    Stop-Transcript | Out-Null
}

Write-Host "Pipeline status: $($state.status)"
Write-Host "Reports and independent position monitors may continue under output/ and logs/."
