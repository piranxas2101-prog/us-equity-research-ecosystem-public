param([switch]$SkipDependencies)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root
$runtime = ".venv-next\Scripts\python.exe"
if (-not (Test-Path $runtime)) {
    # Python 3.12 from the 'py' launcher when it is there; otherwise the python.org per-user install or 'python' on
    # PATH, each checked to really be 3.12 (a fresh-clone test on 2026-10-22 had Python 3.12 without 'py').
    $candidates = @()
    if (Get-Command py -ErrorAction SilentlyContinue) { $candidates += , @("py", "-3.12") }
    $candidates += , @((Join-Path $env:LOCALAPPDATA "Programs\Python\Python312\python.exe"))
    if (Get-Command python -ErrorAction SilentlyContinue) { $candidates += , @("python") }
    $chosen = $null
    foreach ($candidate in $candidates) {
        $exe = $candidate[0]
        $extra = @($candidate | Select-Object -Skip 1)
        if (($exe -ne "py") -and ($exe -ne "python") -and -not (Test-Path $exe)) { continue }
        $version = ""
        try { $version = (& $exe @extra -c "import sys; print('%d.%d' % sys.version_info[:2])" 2>$null) } catch { }
        if ("$version".Trim() -eq "3.12") { $chosen = $candidate; break }
    }
    if ($null -eq $chosen) { throw "Python 3.12 was not found. Install it from python.org (3.12), then run this again." }
    $exe = $chosen[0]
    $extra = @($chosen | Select-Object -Skip 1)
    & $exe @extra -m venv .venv-next
    if (-not (Test-Path $runtime)) { throw "Could not create the .venv-next runtime." }
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
