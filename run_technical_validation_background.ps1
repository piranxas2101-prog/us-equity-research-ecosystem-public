$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $projectRoot
$pythonPath = Join-Path $projectRoot ".venv-next\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $pythonPath)) { throw "Locked .venv-next runtime is required." }
New-Item -ItemType Directory -Force -Path (Join-Path $projectRoot "output") | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $projectRoot "logs") | Out-Null
$stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$outLog = Join-Path $projectRoot "logs\technical_validation_$stamp.out.log"
$errLog = Join-Path $projectRoot "logs\technical_validation_$stamp.err.log"
$process = Start-Process -FilePath "C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe" `
    -ArgumentList @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", ".\technical_validation_worker.ps1") `
    -WorkingDirectory $projectRoot -RedirectStandardOutput $outLog -RedirectStandardError $errLog `
    -WindowStyle Hidden -PassThru
[ordered]@{
    schema = "ecosystem.technical_validation_launcher.v1"
    status = "launched"
    started_at = (Get-Date -Format o)
    pid = $process.Id
    research_only = $true
    paper_submission = $false
    out_log = $outLog
    err_log = $errLog
} | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath "output\technical_validation_launcher_latest.json" -Encoding UTF8
Write-Host "Technical validation launched independently."
Write-Host "PID: $($process.Id)"
Write-Host "Completion state: output/technical_validation_state_latest.json"
