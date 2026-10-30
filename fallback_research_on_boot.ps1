# Fallback research when the computer is opened (owner's decision 2026-10-30), run by the scheduled task
# "Ecosystem Fallback Research" (register_fallback_research_task.ps1: at log on, on unlock and on resume from sleep or
# hibernation). fallback_research.py decides - once a day, 07:00 Athens to 14:00 New York on weekdays, only when
# today's nightly run did not complete and positions are held - and starts the research-only run on the last good
# shortlist and the held positions in the background. The afternoon session waits for it and trades only its symbols.
$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $projectRoot
$logDir = Join-Path $projectRoot "logs"
New-Item -ItemType Directory -Path $logDir -Force | Out-Null
$log = Join-Path $logDir ("fallback_research_on_boot_" + (Get-Date -Format "yyyyMMdd") + ".log")
$python = Join-Path $projectRoot ".venv-next\Scripts\python.exe"

function Write-Log([string]$Text) {
    "$((Get-Date).ToString('s')) $Text" | Add-Content -LiteralPath $log -Encoding UTF8
}

# stderr of a native program must not end the script under Windows PowerShell 5.1 (tests/test_powershell_native_calls.py)
function Invoke-Logged([scriptblock]$Block) {
    $ErrorActionPreference = "Continue"
    & $Block *>&1 | ForEach-Object { "$_" } | Add-Content -LiteralPath $log -Encoding UTF8
    return $LASTEXITCODE
}

function Show-Notice([string]$Title, [string]$Text) {
    try {
        Add-Type -AssemblyName System.Windows.Forms
        Add-Type -AssemblyName System.Drawing
        $n = New-Object System.Windows.Forms.NotifyIcon
        $n.Icon = [System.Drawing.SystemIcons]::Information
        $n.Visible = $true
        $n.BalloonTipTitle = $Title
        $n.BalloonTipText = $Text
        $n.ShowBalloonTip(15000)
        Start-Sleep -Seconds 16
        $n.Dispose()
    } catch { }
}

$recordPath = Join-Path $projectRoot "output\fallback_research_latest.json"
$before = $null
try { $before = (Get-Content -LiteralPath $recordPath -Raw | ConvertFrom-Json).run_id } catch { }
$code = Invoke-Logged { & $python -W ignore "fallback_research.py" "ensure" "--trigger" "boot" }
Write-Log "Fallback research check ended with exit code $code."
$record = $null
try { $record = Get-Content -LiteralPath $recordPath -Raw | ConvertFrom-Json } catch { }
if ($null -ne $record -and $record.run_id -ne $before) {
    Show-Notice "Efedriki erevna" "To nychterino run den oloklirothike: erevna mono gia shortlist kai anoixtes theseis ($($record.symbols) metoxes, $($record.run_id))."
}
