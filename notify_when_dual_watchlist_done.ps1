param(
    [int]$PidToWatch = 0,
    [string]$PointerPath = ".\output\dual_watchlist_latest_background.json",
    [string]$OutputDir = ".\output",
    [string]$LogDir = ".\logs"
)

$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $projectRoot

if (-not (Test-Path $OutputDir)) {
    New-Item -ItemType Directory -Path $OutputDir -Force | Out-Null
}
if (-not (Test-Path $LogDir)) {
    New-Item -ItemType Directory -Path $LogDir -Force | Out-Null
}

if ($PidToWatch -le 0) {
    if (-not (Test-Path $PointerPath)) {
        throw "Missing pointer file and no PidToWatch supplied: $PointerPath"
    }
    $pointer = Get-Content $PointerPath -Raw | ConvertFrom-Json
    $PidToWatch = [int]$pointer.pid
}

$stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$logPath = Join-Path $LogDir "dual_watchlist_completion_notifier_$stamp.log"

function Log-Line {
    param([string]$Message)
    $line = "[{0}] {1}" -f (Get-Date -Format o), $Message
    Add-Content -Path $logPath -Value $line -Encoding UTF8
}

Log-Line "Watching PID $PidToWatch"

while ($true) {
    $proc = Get-Process -Id $PidToWatch -ErrorAction SilentlyContinue
    if ($null -eq $proc) {
        break
    }
    Start-Sleep -Seconds 20
}

$finishedPath = Join-Path $OutputDir "RUN_FINISHED_READ_ME.txt"
$summary = Get-ChildItem $OutputDir -Filter "dual_watchlist_summary_*.md" |
    Sort-Object LastWriteTime -Descending |
    Select-Object -First 1
$meeting = Get-ChildItem $OutputDir -Filter "meeting_team_daily_*.md" |
    Sort-Object LastWriteTime -Descending |
    Select-Object -First 1

$lines = @(
    "Babis + Giannis + Yankos + Periklis + Petros background process finished.",
    "Finished at: $(Get-Date -Format o)",
    "Watched PID: $PidToWatch",
    "Latest summary: $($summary.FullName)",
    "Latest meeting: $($meeting.FullName)",
    "Giannis latest: $(Join-Path $projectRoot 'output\giannis_rule_based_latest.md')",
    "Babis latest: $(Join-Path $projectRoot 'output\babis_shortlist_latest.json')",
    "Yankos latest: $(Join-Path $projectRoot 'output\yankos_strategy_latest.md')",
    "Periklis latest: $(Join-Path $projectRoot 'output\periklis_strategy_latest.md')",
    "Strategy backtest latest: $(Join-Path $projectRoot 'output\strategy_backtest_latest.md')",
    "Petros latest: $(Join-Path $projectRoot 'output\petros_review_latest.md')"
)
$lines | Set-Content -Path $finishedPath -Encoding UTF8

try {
    [console]::beep(880, 500)
    Start-Sleep -Milliseconds 120
    [console]::beep(988, 500)
    Start-Sleep -Milliseconds 120
    [console]::beep(1175, 900)
} catch {
    try {
        Add-Type -AssemblyName System.Windows.Forms
        [System.Media.SystemSounds]::Asterisk.Play()
    } catch {
        Log-Line "Audio notification unavailable: $($_.Exception.Message)"
    }
}

try {
    Add-Type -AssemblyName System.Windows.Forms
    [System.Windows.Forms.MessageBox]::Show(
        "Babis + Giannis + Yankos + Periklis + Petros finished. Reports are ready.",
        "Market scan completed",
        [System.Windows.Forms.MessageBoxButtons]::OK,
        [System.Windows.Forms.MessageBoxIcon]::Information
    ) | Out-Null
} catch {
    Log-Line "Popup notification unavailable: $($_.Exception.Message)"
}

Log-Line "Notification complete. Marker: $finishedPath"
