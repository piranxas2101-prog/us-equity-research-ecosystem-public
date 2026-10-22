param(
    [int]$UniverseLimit = 5,
    [switch]$SkipExtensionInstall
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $projectRoot
$outputDir = Join-Path $projectRoot "output"
$logDir = Join-Path $projectRoot "logs"
New-Item -ItemType Directory -Path $outputDir -Force | Out-Null
New-Item -ItemType Directory -Path $logDir -Force | Out-Null
$pointer = Join-Path $outputDir "architecture_upgrade_background_latest.json"
$extensionVenv = Join-Path $projectRoot ".architecture-venv"
$extensionPython = Join-Path $extensionVenv "Scripts\python.exe"
$basePython = Join-Path $env:LOCALAPPDATA "Programs\Python\Python312\python.exe"

$state = [ordered]@{
    schema = "ecosystem.architecture_upgrade_background.v1"
    started_at = (Get-Date -Format o)
    pid = $PID
    universe_limit = $UniverseLimit
    research_only = $true
    paper_submission_requested = $false
    live_money = $false
    status = "running_pipeline"
}
$state | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $pointer -Encoding UTF8

try {
    & ".\run_daily_dual_watchlist.ps1" `
        -LockedWatchlistPath ".\architecture_upgrade_watchlist.json" `
        -MeetingDurationMinutes 0 `
        -NoAutoTraderStart
    if ($LASTEXITCODE -ne 0) { throw "Research pipeline failed with exit code $LASTEXITCODE" }

    $state.status = "installing_extensions"
    $state | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $pointer -Encoding UTF8
    if (-not $SkipExtensionInstall) {
        if (-not (Test-Path -LiteralPath $extensionPython)) {
            & $basePython -m venv --system-site-packages $extensionVenv
            if ($LASTEXITCODE -ne 0) { throw "Could not create isolated extension environment" }
        }
        & $extensionPython -m pip install --disable-pip-version-check -r requirements-architecture-runtime.txt
        if ($LASTEXITCODE -ne 0) { throw "Optional extension installation failed" }
    }
    if (-not (Test-Path -LiteralPath $extensionPython)) {
        $extensionPython = $basePython
    }

    $state.status = "running_architecture_sample"
    $state | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $pointer -Encoding UTF8
    & $extensionPython architecture_upgrade_sample.py `
        --env-file .env `
        --db data\market_intel.db `
        --fundamental-packet output\fundamental_research_packet_latest.json `
        --document output\fundamental_research_packet_latest.md `
        --output output\architecture_upgrade_sample_latest.json
    $sampleExit = $LASTEXITCODE

    $state.status = "validating_dagster_definitions"
    $state | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $pointer -Encoding UTF8
    & $extensionPython -c "from dagster_research_assets import defs; assert defs is not None; print('Dagster definitions loaded')"
    $dagsterExit = $LASTEXITCODE

    $state.sample_exit_code = $sampleExit
    $state.dagster_validation_exit_code = $dagsterExit
    $state.status = if ($sampleExit -eq 0 -and $dagsterExit -eq 0) { "completed" } else { "completed_with_extension_errors" }
} catch {
    $state.status = "failed"
    $state.error = $_.Exception.Message
} finally {
    $state.finished_at = (Get-Date -Format o)
    $state.result = "output\architecture_upgrade_sample_latest.json"
    $state | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $pointer -Encoding UTF8
}

if ($state.status -eq "failed") { exit 1 }
exit 0
