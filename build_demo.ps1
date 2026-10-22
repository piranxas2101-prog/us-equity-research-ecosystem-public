$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root
$runtime = ".\.venv-next\Scripts\python.exe"
if (-not (Test-Path $runtime)) { throw "Run .\setup_windows.ps1 first." }
& $runtime agent_learning_control_plane.py dashboard
& $runtime symbol_research_dashboard.py
& $runtime analyst_report.py
& $runtime research_portal.py
if ($LASTEXITCODE -ne 0) { throw "Demo artifact generation failed." }
Write-Host "Demo ready: output\dashboards\index.html" -ForegroundColor Green
