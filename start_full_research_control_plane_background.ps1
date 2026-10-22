param(
    [int]$UniverseLimit = 0,
    [int]$FundamentalControlPlaneLimit = 0
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $projectRoot
if (-not (Test-Path -LiteralPath (Join-Path $projectRoot ".venv-next\Scripts\python.exe"))) {
    throw "The locked .venv-next Python runtime is required before background launch."
}
$outputDir = Join-Path $projectRoot "output"
$logDir = Join-Path $projectRoot "logs"
New-Item -ItemType Directory -Path $outputDir -Force | Out-Null
New-Item -ItemType Directory -Path $logDir -Force | Out-Null
$pointer = Join-Path $outputDir "full_research_control_plane_background_latest.json"
$launcherPointer = Join-Path $outputDir "full_research_control_plane_launcher_latest.json"

if (Test-Path -LiteralPath $pointer) {
    try {
        $existing = Get-Content -LiteralPath $pointer -Raw | ConvertFrom-Json
        if ($existing.status -notin @("completed", "completed_review_required", "failed")) {
            $existingProcess = Get-Process -Id ([int]$existing.pid) -ErrorAction SilentlyContinue
            if ($null -ne $existingProcess) {
                Write-Host "Full research control-plane run already active (PID $($existing.pid))."
                exit 0
            }
        }
    } catch {}
}

$pythonPath = Join-Path $projectRoot ".venv-next\Scripts\python.exe"
Write-Host "Refreshing and reconciling all listed symbols (no market scan or agents are started)..."
& $pythonPath ".\prepare_full_research_universe.py" --refresh-sources
if ($LASTEXITCODE -ne 0) {
    throw "Full listed-universe preparation failed. No research agent was started."
}
Write-Host "Running full-research preflight (no providers or agents are started)..."
& $pythonPath ".\full_research_preflight.py" --output "output/full_research_preflight_latest.json"
if ($LASTEXITCODE -ne 0) {
    throw "Full-research preflight blocked launch. See output/full_research_preflight_latest.json"
}

$timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
$outLog = Join-Path $logDir "full_research_control_plane_$timestamp.out.log"
$errLog = Join-Path $logDir "full_research_control_plane_$timestamp.err.log"
$powershell = "C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"
$arguments = @(
    "-NoProfile", "-ExecutionPolicy", "Bypass",
    "-File", ".\run_full_research_control_plane.ps1",
    "-UniverseLimit", [string]$UniverseLimit,
    "-FundamentalControlPlaneLimit", [string]$FundamentalControlPlaneLimit,
    "-CompletionReportPath", "output/full_market_completion_latest.md"
)
$process = Start-Process `
    -FilePath $powershell `
    -ArgumentList $arguments `
    -WorkingDirectory $projectRoot `
    -RedirectStandardOutput $outLog `
    -RedirectStandardError $errLog `
    -WindowStyle Hidden `
    -PassThru

$state = [ordered]@{
    schema = "ecosystem.full_research_control_plane_launcher.v1"
    started_at = (Get-Date -Format o)
    pid = $process.Id
    universe_limit = $UniverseLimit
    fundamental_control_plane_limit = $FundamentalControlPlaneLimit
    research_only = $true
    paper_submission_requested = $false
    live_money = $false
    out_log = $outLog
    err_log = $errLog
    status = "launched"
}
$state | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $launcherPointer -Encoding UTF8

Write-Host "Full research control-plane run launched independently."
Write-Host "PID: $($process.Id)"
Write-Host "State: $pointer"
Write-Host "Launcher record: $launcherPointer"
Write-Host "Out log: $outLog"
Write-Host "Err log: $errLog"
