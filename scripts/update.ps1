# Stops the bot, pulls the latest version from GitHub,
# installs new requirements if needed, and starts the bot again.

. "$PSScriptRoot\common.ps1"
Assert-Admin $PSCommandPath
$ErrorActionPreference = 'Stop'

Write-Host ''
Write-Host '  Telegram Translator - update' -ForegroundColor White
Write-Host ''

function Invoke-Git {
    # safe.directory avoids "dubious ownership" errors when run as administrator
    & git -C $ProjectDir -c "safe.directory=*" @args
    if ($LASTEXITCODE -ne 0) { throw "git $($args -join ' ') failed (exit code $LASTEXITCODE)" }
}

$stopped = $false
try {
    if (-not (Get-Command git -ErrorAction SilentlyContinue)) { throw 'git is not installed or not in PATH.' }
    if (-not (Test-TaskInstalled)) { throw 'Service is not installed. Run install-service.cmd first.' }

    $changed = & git -C $ProjectDir -c "safe.directory=*" status --porcelain --untracked-files=no
    if ($changed) {
        throw "Local changes in tracked files, update cancelled:`n$($changed -join "`n")`nMove your settings to .env, then run: git checkout -- <file>"
    }

    Write-Step 'Stopping bot...'
    Stop-Bot
    $stopped = $true

    Write-Step 'Pulling latest version...'
    $reqFile   = Join-Path $ProjectDir 'requirements.txt'
    $reqBefore = (Get-FileHash $reqFile).Hash
    $oldHead   = (& git -C $ProjectDir -c "safe.directory=*" rev-parse --short HEAD)
    Invoke-Git pull --ff-only --quiet
    $newHead   = (& git -C $ProjectDir -c "safe.directory=*" rev-parse --short HEAD)

    if ((Get-FileHash $reqFile).Hash -ne $reqBefore) {
        Write-Step 'Installing new requirements...'
        & $Python -m pip install --quiet --disable-pip-version-check -r $reqFile
        if ($LASTEXITCODE -ne 0) { throw 'pip install failed.' }
    }

    Write-Step 'Starting bot...'
    $since = Get-Date
    Start-Bot
    $err = Wait-BotStarted $since
    if ($err) { throw $err }

    if ($oldHead -eq $newHead) {
        Show-Result $true "Already up to date ($newHead). Bot restarted."
    } else {
        Show-Result $true "Updated $oldHead -> $newHead. Bot is running."
    }
} catch {
    $message = $_.Exception.Message
    if ($stopped) {
        # Update failed after stopping: bring the previous version back up
        try { Start-Bot; $message += "`n(Previous version restarted.)" } catch { }
    }
    Show-Result $false $message
}
