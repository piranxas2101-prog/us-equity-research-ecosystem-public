param(
    [string]$LockedWatchlistPath = ".\output\watchlist_300_locked.json",
    [switch]$RebuildLock,
    [switch]$FullMarket,
    [int]$MeetingDurationMinutes = 0
)

$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $projectRoot

$outputDir = Join-Path $projectRoot "output"
$logDir = Join-Path $projectRoot "logs"
if (-not (Test-Path $outputDir)) {
    New-Item -ItemType Directory -Path $outputDir -Force | Out-Null
}
if (-not (Test-Path $logDir)) {
    New-Item -ItemType Directory -Path $logDir -Force | Out-Null
}

$pointerPath = Join-Path $outputDir "dual_watchlist_latest_background.json"
if (Test-Path $pointerPath) {
    try {
        $existing = Get-Content $pointerPath -Raw | ConvertFrom-Json
        $existingPid = [int]$existing.pid
        $existingProc = Get-Process -Id $existingPid -ErrorAction SilentlyContinue
        if ($null -ne $existingProc) {
            Write-Host "Dual watchlist background run already active (PID $existingPid)."
            Write-Host "Use .\check_dual_watchlist_status.ps1 or .\stop_dual_watchlist_background.ps1 first."
            exit 0
        }
    } catch {
        # Ignore stale or malformed pointer and continue.
    }
}

$ts = Get-Date -Format "yyyyMMdd_HHmmss"
$outLog = Join-Path $logDir "dual_watchlist_background_$ts.out.log"
$errLog = Join-Path $logDir "dual_watchlist_background_$ts.err.log"

$args = @(
    "-ExecutionPolicy", "Bypass",
    "-File", ".\run_daily_dual_watchlist.ps1",
    "-LockedWatchlistPath", $LockedWatchlistPath,
    "-MeetingDurationMinutes", "$MeetingDurationMinutes"
)
if ($RebuildLock) {
    $args += "-RebuildLock"
}
if ($FullMarket) {
    $args += "-FullMarket"
}

$proc = Start-Process `
    -FilePath "C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe" `
    -ArgumentList $args `
    -WorkingDirectory $projectRoot `
    -RedirectStandardOutput $outLog `
    -RedirectStandardError $errLog `
    -WindowStyle Hidden `
    -PassThru

Start-Sleep -Seconds 2

$payload = [ordered]@{
    started_at = (Get-Date -Format o)
    pid = $proc.Id
    locked_watchlist = $LockedWatchlistPath
    rebuild_lock = [bool]$RebuildLock
    full_market = [bool]$FullMarket
    meeting_duration_minutes = $MeetingDurationMinutes
    out_log = $outLog
    err_log = $errLog
    command = "run_daily_dual_watchlist.ps1"
}
$payload | ConvertTo-Json -Depth 6 | Set-Content -Path $pointerPath -Encoding UTF8

Write-Host "Dual watchlist background run started."
Write-Host "PID: $($proc.Id)"
Write-Host "Out log: $outLog"
Write-Host "Err log: $errLog"
Write-Host "Pointer: $pointerPath"
