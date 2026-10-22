$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $projectRoot

$env:ECOSYSTEM_RUN_ID = "run_20260825_160646"

Write-Host "RESUMING PAPER-TRADER AGENTS ONLY"
Write-Host "Run ID: $env:ECOSYSTEM_RUN_ID"
Write-Host "Frozen inputs: Babis, Giannis, Yankos, Periklis, Petros and Koulis artifacts from the completed research run."
Write-Host "No research scan will be restarted. Live-money execution is not enabled."

& ".\start_trader_paper_trading_today.ps1" `
    -MaxOrders 1000 `
    -MaxShortOrders 1000 `
    -MinScore 0 `
    -NotionalPerTrade 250 `
    -MiltosPollSeconds 60 `
    -MiltosMaxCycles 330

if ($LASTEXITCODE -ne 0) {
    throw "Trader-agent resume failed with exit code $LASTEXITCODE."
}

Write-Host "Trader-agent handoff completed. Position monitors and Miltos may continue in background."
