param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$PythonArgs
)

$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$candidates = @(
    (Join-Path $projectRoot ".venv-next\Scripts\python.exe"),
    (Join-Path $projectRoot ".venv\Scripts\python.exe"),
    (Join-Path $env:LOCALAPPDATA "Programs\Python\Python312\python.exe"),
    "python"
)

$pythonPath = $null
foreach ($candidate in $candidates) {
    try {
        if ($candidate -eq "python") {
            $cmd = Get-Command python -ErrorAction SilentlyContinue
            if ($null -ne $cmd) {
                $pythonPath = $cmd.Source
                break
            }
        } elseif (Test-Path -LiteralPath $candidate) {
            $pythonPath = $candidate
            break
        }
    } catch {
        continue
    }
}

if ([string]::IsNullOrWhiteSpace($pythonPath)) {
    throw "Python not found. Install Python 3.12 or create .venv\Scripts\python.exe."
}

& $pythonPath @PythonArgs
exit $LASTEXITCODE
