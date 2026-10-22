param(
    [string]$ProjectRoot = $PSScriptRoot,
    [ValidatePattern('^[A-Za-z0-9_.-]+$')][string]$RunId,
    [string]$OutputPath,
    [switch]$NoOpen
)

$ErrorActionPreference = 'Stop'
$project = (Resolve-Path -LiteralPath $ProjectRoot).Path
$scriptPath = Join-Path $PSScriptRoot 'symbol_research_dashboard.py'

$candidates = @(
    (Join-Path $project '.venv-next\Scripts\python.exe'),
    (Join-Path $project '.venv\Scripts\python.exe'),
    (Join-Path $env:LOCALAPPDATA 'Programs\Python\Python312\python.exe')
)
$systemPython = Get-Command python -ErrorAction SilentlyContinue
if ($systemPython) { $candidates += $systemPython.Source }

$python = $null
foreach ($candidate in $candidates) {
    if (-not (Test-Path -LiteralPath $candidate)) { continue }
    try {
        $null = & $candidate --version 2>$null
        if ($LASTEXITCODE -eq 0) { $python = $candidate; break }
    } catch { continue }
}
if (-not $python) { throw 'No working Python interpreter was found.' }

if ($RunId) {
    $manifest = Join-Path $project "output\runs\$RunId\manifest.json"
    if (-not (Test-Path -LiteralPath $manifest)) { throw "Run manifest not found: $manifest" }
} else {
    $manifest = $null
}

if ($OutputPath) {
    $output = $OutputPath
} else {
    $name = if ($RunId) { "symbol_research_$RunId.html" } else { 'symbol_research_latest.html' }
    $output = Join-Path $project "output\dashboards\$name"
}

$arguments = @($scriptPath, '--project-root', $project, '--output', $output)
if ($manifest) { $arguments += @('--manifest', $manifest) }
& $python @arguments
if ($LASTEXITCODE -ne 0) { throw "Dashboard generation failed with exit code $LASTEXITCODE" }

$resolvedOutput = (Resolve-Path -LiteralPath $output).Path
if (-not $NoOpen) { Start-Process -FilePath $resolvedOutput }
Write-Output "Dashboard: $resolvedOutput"
