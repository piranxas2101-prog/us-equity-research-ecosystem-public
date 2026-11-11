param(
    [Parameter(Mandatory = $true)]
    [string]$StatePath
)

$terminalStates = @("completed", "completed_review_required", "failed")
try { $state = Get-Content -LiteralPath $StatePath -Raw | ConvertFrom-Json }
catch { exit 1 }
if ($terminalStates -notcontains $state.status) { exit 1 }

# A source the run reused from its last good file (acquire_universe.py, owner 2026-11-11) is said in the notice.
$staleNote = ""
try {
    $sourcesPath = Join-Path $PSScriptRoot "data\research_universe_sources\manifest.json"
    # Windows PowerShell 5.1 passes a JSON array down the pipeline as one object: unroll it first
    $rows = @(Get-Content -LiteralPath $sourcesPath -Raw | ConvertFrom-Json)
    $stale = @($rows | ForEach-Object { $_ } | Where-Object { $_.status -eq "stale_reused" })
    foreach ($row in $stale) {
        $staleNote += " Old source file used: $($row.name), $([math]::Round([double]$row.age_hours)) h old."
    }
} catch { $staleNote = "" }

try {
    Add-Type -AssemblyName System.Windows.Forms
    Add-Type -AssemblyName System.Drawing
    $notification = New-Object System.Windows.Forms.NotifyIcon
    $notification.Icon = [System.Drawing.SystemIcons]::Information
    $notification.Visible = $true
    # ASCII keeps this launcher parse-safe under Windows PowerShell 5.1 even
    # when a checkout loses its UTF-8 BOM.
    $notification.BalloonTipTitle = "Full research run finished"
    $notification.BalloonTipText = "Status: $($state.status). Report: output/research_run_receipt_latest.md.$staleNote"
    $notification.ShowBalloonTip(12000)
    Start-Sleep -Seconds 14
    $notification.Dispose()
} catch {
    try { [console]::beep(988, 700) } catch {}
}
