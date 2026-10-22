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

$outputDir = Join-Path $projectRoot "output"
$logDir = Join-Path $projectRoot "logs"
New-Item -ItemType Directory -Path $outputDir -Force | Out-Null
New-Item -ItemType Directory -Path $logDir -Force | Out-Null

$launcherPointer = Join-Path $outputDir "full_ecosystem_launcher_latest.json"
$pipelinePointer = Join-Path $outputDir "full_ecosystem_test_latest.json"

foreach ($pointer in @($launcherPointer, $pipelinePointer)) {
    if (-not (Test-Path -LiteralPath $pointer)) {
        continue
    }
    try {
        $existing = Get-Content -LiteralPath $pointer -Raw | ConvertFrom-Json
        $existingPid = [int]$existing.pid
        if ($null -ne (Get-Process -Id $existingPid -ErrorAction SilentlyContinue)) {
            Write-Host "Full ecosystem already active (PID $existingPid)."
            exit 0
        }
    } catch {
        # A stale or malformed pointer must not prevent a fresh controlled run.
    }
}

$timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
$outLog = Join-Path $logDir "full_ecosystem_launcher_$timestamp.out.log"
$errLog = Join-Path $logDir "full_ecosystem_launcher_$timestamp.err.log"
$arguments = @(
    "-NoProfile",
    "-ExecutionPolicy", "Bypass",
    "-File", ".\start_full_ecosystem_sample.ps1",
    "-UniverseLimit", [string]$UniverseLimit,
    "-MaxOrders", [string]$MaxOrders,
    "-MaxShortOrders", [string]$MaxShortOrders,
    "-MinScore", [string]$MinScore,
    "-NotionalPerTrade", [string]$NotionalPerTrade
)

$process = Start-Process `
    -FilePath "C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe" `
    -ArgumentList $arguments `
    -WorkingDirectory $projectRoot `
    -RedirectStandardOutput $outLog `
    -RedirectStandardError $errLog `
    -WindowStyle Hidden `
    -PassThru

$state = [ordered]@{
    schema = "ecosystem.full_test_launcher.v1"
    started_at = (Get-Date -Format o)
    pid = $process.Id
    universe_limit = $UniverseLimit
    max_orders = $MaxOrders
    max_short_orders = $MaxShortOrders
    min_score = $MinScore
    notional_per_trade = $NotionalPerTrade
    live_money = $false
    paper_only = $true
    out_log = $outLog
    err_log = $errLog
    status = "launched"
}
$state | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $launcherPointer -Encoding UTF8

Write-Host "Full ecosystem paper-only launcher started."
Write-Host "PID: $($process.Id)"
Write-Host "Pointer: $launcherPointer"
Write-Host "Out log: $outLog"
Write-Host "Err log: $errLog"
