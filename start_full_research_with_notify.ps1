# Start a research-only full run in the background (prepare, preflight, detached run)
# and show a Windows notification if it cannot start. The run itself notifies when it
# ends (notify_full_research_control_plane.ps1). Used by the control center.
$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $projectRoot
$log = Join-Path $projectRoot ("logs\launch_full_research_" + (Get-Date -Format "yyyyMMdd_HHmmss") + ".log")

function Show-Note([string]$Title, [string]$Text) {
    try {
        Add-Type -AssemblyName System.Windows.Forms
        Add-Type -AssemblyName System.Drawing
        $n = New-Object System.Windows.Forms.NotifyIcon
        $n.Icon = [System.Drawing.SystemIcons]::Warning
        $n.Visible = $true
        $n.BalloonTipTitle = $Title
        $n.BalloonTipText = $Text
        $n.ShowBalloonTip(15000)
        Start-Sleep -Seconds 16
        $n.Dispose()
    } catch { try { [console]::beep(440, 900) } catch {} }
}

try {
    # Prepare (with carry-forward, reassessment and review desk), preflight, then the
    # detached research-only run; that run shows its own notification when it ends.
    & ".\start_full_research_control_plane_background.ps1" *>&1 | Tee-Object -FilePath $log
} catch {
    "$($_.Exception.Message)" | Add-Content -LiteralPath $log
    Show-Note "Full research run did not start" "Preparation or preflight failed. See $log"
    exit 1
}
