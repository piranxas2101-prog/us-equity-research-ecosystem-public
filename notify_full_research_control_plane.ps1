param(
    [Parameter(Mandatory = $true)]
    [string]$StatePath
)

$terminalStates = @("completed", "completed_review_required", "failed")
try { $state = Get-Content -LiteralPath $StatePath -Raw | ConvertFrom-Json }
catch { exit 1 }
if ($terminalStates -notcontains $state.status) { exit 1 }

try {
    Add-Type -AssemblyName System.Windows.Forms
    Add-Type -AssemblyName System.Drawing
    $notification = New-Object System.Windows.Forms.NotifyIcon
    $notification.Icon = [System.Drawing.SystemIcons]::Information
    $notification.Visible = $true
    # ASCII keeps this launcher parse-safe under Windows PowerShell 5.1 even
    # when a checkout loses its UTF-8 BOM.
    $notification.BalloonTipTitle = "Full research run finished"
    $notification.BalloonTipText = "Status: $($state.status). Report: output/research_run_receipt_latest.md"
    $notification.ShowBalloonTip(12000)
    Start-Sleep -Seconds 14
    $notification.Dispose()
} catch {
    try { [console]::beep(988, 700) } catch {}
}
