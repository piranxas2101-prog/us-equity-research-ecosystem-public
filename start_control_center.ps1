# Open the local control center (http://127.0.0.1:8770). Starts the server in the
# background if it is not already running; it listens on this computer only.
$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$python = Join-Path $projectRoot ".venv-next\Scripts\python.exe"
$url = "http://127.0.0.1:8770/"
if (-not (Test-Path -LiteralPath $python)) { throw "The locked .venv-next Python runtime is required." }

$listening = Get-NetTCPConnection -LocalAddress 127.0.0.1 -LocalPort 8770 -State Listen -ErrorAction SilentlyContinue
if (-not $listening) {
    $logDir = Join-Path $projectRoot "logs"
    New-Item -ItemType Directory -Path $logDir -Force | Out-Null
    Start-Process -FilePath $python `
        -ArgumentList @("control_center.py", "--no-browser", "--port", "8770") `
        -WorkingDirectory $projectRoot -WindowStyle Hidden `
        -RedirectStandardOutput (Join-Path $logDir "control_center.out.log") `
        -RedirectStandardError (Join-Path $logDir "control_center.err.log") | Out-Null
    Start-Sleep -Seconds 2
}
Start-Process $url
