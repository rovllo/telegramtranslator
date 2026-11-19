# Runs the bot and restarts it whenever it exits.
# Started in the background by the scheduled task; not meant to be run by hand.

$ProjectDir = Split-Path -Parent $PSScriptRoot
$Python     = Join-Path $ProjectDir 'venv\Scripts\python.exe'
$LogDir     = Join-Path $ProjectDir 'logs'
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

while ($true) {
    $proc = Start-Process -FilePath $Python -ArgumentList 'bot.py' `
        -WorkingDirectory $ProjectDir -NoNewWindow -Wait -PassThru `
        -RedirectStandardOutput (Join-Path $LogDir 'stdout.log') `
        -RedirectStandardError  (Join-Path $LogDir 'crash.log')

    $stamp = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'
    Add-Content -Path (Join-Path $LogDir 'runner.log') `
        -Value "$stamp bot exited with code $($proc.ExitCode); restarting in 10 seconds"
    Start-Sleep -Seconds 10
}
