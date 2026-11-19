# Stops the bot and removes the background service.

. "$PSScriptRoot\common.ps1"
Assert-Admin $PSCommandPath
$ErrorActionPreference = 'Stop'

Write-Host ''
Write-Host '  Telegram Translator - uninstall service' -ForegroundColor White
Write-Host ''

try {
    Write-Step 'Stopping bot...'
    Stop-Bot
    if (Test-TaskInstalled) {
        Write-Step 'Removing scheduled task...'
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    }
    Show-Result $true 'Service removed. The bot will no longer start with Windows.'
} catch {
    Show-Result $false $_.Exception.Message
}
