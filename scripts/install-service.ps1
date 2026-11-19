# Creates the background service (scheduled task) that starts the bot
# at Windows startup, without needing anyone to log in, and starts it now.

. "$PSScriptRoot\common.ps1"
Assert-Admin $PSCommandPath
$ErrorActionPreference = 'Stop'

Write-Host ''
Write-Host '  Telegram Translator - install service' -ForegroundColor White
Write-Host ''

try {
    if (-not (Test-Path $Python)) {
        throw "venv not found ($Python). Run: py -m venv venv; .\venv\Scripts\pip install -r requirements.txt"
    }
    if (-not (Test-Path (Join-Path $ProjectDir '.env'))) {
        throw ".env not found. Copy .env.example to .env and fill it in."
    }

    Write-Step 'Stopping running bot (if any)...'
    Stop-Bot

    Write-Step 'Creating scheduled task...'
    $runner = Join-Path $PSScriptRoot 'run-bot.ps1'
    $action = New-ScheduledTaskAction -Execute 'powershell.exe' `
        -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$runner`"" `
        -WorkingDirectory $ProjectDir
    $trigger   = New-ScheduledTaskTrigger -AtStartup
    $principal = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest
    $settings  = New-ScheduledTaskSettingsSet `
        -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable `
        -ExecutionTimeLimit ([TimeSpan]::Zero) `
        -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) `
        -MultipleInstances IgnoreNew

    Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
        -Principal $principal -Settings $settings -Force `
        -Description 'Telegram channel translator bot' | Out-Null

    Write-Step 'Starting bot...'
    $since = Get-Date
    Start-Bot
    $err = Wait-BotStarted $since
    if ($err) { throw $err }

    Show-Result $true "Service '$TaskName' installed and running. It will start automatically after reboot."
} catch {
    Show-Result $false $_.Exception.Message
}
