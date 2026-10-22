$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $projectRoot
& (Join-Path $projectRoot "run_py.ps1") "research_session_control.py" "start"
exit $LASTEXITCODE
