$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $projectRoot

$latestPath = Join-Path $projectRoot "output\babis_shortlist_latest.json"
if (-not (Test-Path $latestPath)) {
    throw "Missing watchlist baseline: $latestPath"
}

$latest = Get-Content $latestPath -Raw | ConvertFrom-Json
$tickers = @($latest.shortlist | Select-Object -ExpandProperty ticker)
if ($tickers.Count -eq 0) {
    throw "No tickers found in $latestPath"
}

$pythonPath = Join-Path $projectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $pythonPath)) {
    $globalPython = "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe"
    if (Test-Path $globalPython) {
        $pythonPath = $globalPython
    } else {
        $pythonPath = "python"
    }
}

$timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
$logDir = Join-Path $projectRoot "logs"
if (-not (Test-Path $logDir)) {
    New-Item -ItemType Directory -Path $logDir | Out-Null
}
$logPath = Join-Path $logDir "watchlist_refresh_$timestamp.log"

$cmdArgs = @(
    "us_equity_monitor.py",
    "--config", "scanner_config.json",
    "pipeline",
    "--stage1-limit", "300",
    "--stage2-limit", "0",
    "--stage2-llm-limit", "0",
    "--no-stage1-llm",
    "--no-stage2-llm",
    "--tickers"
) + $tickers

$previousErrorAction = $ErrorActionPreference
$ErrorActionPreference = "Continue"
& $pythonPath @cmdArgs 2>&1 | Tee-Object -FilePath $logPath
$exitCode = $LASTEXITCODE
$ErrorActionPreference = $previousErrorAction

if ($exitCode -ne 0) {
    throw "Watchlist refresh failed with exit code $exitCode. Check log: $logPath"
}

Write-Host "Watchlist refresh completed. Log: $logPath"
