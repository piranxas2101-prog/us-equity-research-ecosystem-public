param([Parameter(Mandatory = $true)][string]$StatePath)

$terminalStates = @('finished', 'finished_with_review_required', 'failed', 'stopped_with_pending')
do {
    Start-Sleep -Seconds 10
    try { $state = Get-Content -LiteralPath $StatePath -Raw | ConvertFrom-Json }
    catch { $state = $null }
} while ($null -eq $state -or $terminalStates -notcontains $state.status)

Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
$notification = New-Object System.Windows.Forms.NotifyIcon
$notification.Icon = [System.Drawing.SystemIcons]::Information
$notification.Visible = $true
$notification.BalloonTipTitle = 'Ολοκληρώθηκε το sector evidence pass'
$notification.BalloonTipText = "Κατάσταση: $($state.status) — $($state.processed_symbols)/$($state.total_symbols). Rich descriptions: $($state.rich_descriptions)."
$notification.ShowBalloonTip(10000)
Start-Sleep -Seconds 12
$notification.Dispose()
