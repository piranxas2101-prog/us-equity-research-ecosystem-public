param([int]$UniverseLimit = 5)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $projectRoot
$outputDir = Join-Path $projectRoot "output"
$logDir = Join-Path $projectRoot "logs"
New-Item -ItemType Directory -Path $outputDir -Force | Out-Null
New-Item -ItemType Directory -Path $logDir -Force | Out-Null
$pointer = Join-Path $outputDir "architecture_upgrade_background_latest.json"

if (Test-Path -LiteralPath $pointer) {
    try {
        $existing = Get-Content -LiteralPath $pointer -Raw | ConvertFrom-Json
        if ($existing.status -notin @("completed", "completed_with_extension_errors", "failed")) {
            $process = Get-Process -Id ([int]$existing.pid) -ErrorAction SilentlyContinue
            if ($null -ne $process) {
                Write-Host "Architecture sample already running (PID $($existing.pid))."
                exit 0
            }
        }
    } catch {}
}

$timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
$outLog = Join-Path $logDir "architecture_upgrade_background_$timestamp.out.log"
$errLog = Join-Path $logDir "architecture_upgrade_background_$timestamp.err.log"
$arguments = @(
    "-NoProfile", "-ExecutionPolicy", "Bypass",
    "-File", ".\run_architecture_upgrade_research_sample.ps1",
    "-UniverseLimit", [string]$UniverseLimit
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
    schema = "ecosystem.architecture_upgrade_launcher.v1"
    started_at = (Get-Date -Format o)
    pid = $process.Id
    universe_limit = $UniverseLimit
    research_only = $true
    paper_submission_requested = $false
    live_money = $false
    out_log = $outLog
    err_log = $errLog
    status = "launched"
}
$state | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $pointer -Encoding UTF8
Write-Host "Architecture-upgrade research sample launched in background."
Write-Host "PID: $($process.Id)"
Write-Host "Pointer: $pointer"
Write-Host "Out log: $outLog"
Write-Host "Err log: $errLog"
