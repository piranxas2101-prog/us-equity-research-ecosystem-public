param([switch]$SkipDependencies)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root
$runtime = ".venv-next\Scripts\python.exe"
if (-not (Test-Path $runtime)) {
    $python = Get-Command py -ErrorAction SilentlyContinue
    if (-not $python) { throw "Python launcher 'py' was not found. Install Python 3.12 first." }
    & py -3.12 -m venv .venv-next
}
if (-not $SkipDependencies) {
    & ".\$runtime" -m pip install --require-hashes -r requirements-windows-py312.lock
    if ($LASTEXITCODE -ne 0) { throw "Dependency installation failed." }
}
if (-not (Test-Path ".env")) { Copy-Item ".env.example" ".env" }
& ".\$runtime" market_intel_db.py init
& ".\$runtime" agent_learning_control_plane.py init
& ".\$runtime" -m unittest tests.test_agent_learning_control_plane tests.test_deterministic_valuation tests.test_macro_regime
if ($LASTEXITCODE -ne 0) { throw "Local installation verification failed." }
Write-Host "Local installation is ready. No server is required." -ForegroundColor Green
Write-Host "Edit .env for provider keys, then run .\open_symbol_research_dashboard.ps1 or a research launcher."
