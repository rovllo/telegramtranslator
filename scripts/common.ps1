# Shared settings and helpers for the Windows service scripts.
# Dot-source this file:  . "$PSScriptRoot\common.ps1"

$TaskName   = 'TelegramTranslatorBot'
$ProjectDir = Split-Path -Parent $PSScriptRoot
$Python     = Join-Path $ProjectDir 'venv\Scripts\python.exe'
$LogDir     = Join-Path $ProjectDir 'logs'
$BotLog     = Join-Path $LogDir 'bot.log'
$CrashLog   = Join-Path $LogDir 'crash.log'
$PidFile    = Join-Path $ProjectDir 'bot.pid'
$ResultSeconds = 3

function Assert-Admin([string]$ScriptPath) {
    $identity  = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($identity)
    if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        # Re-launch this script elevated (UAC prompt) and close the current window
        Start-Process powershell.exe -Verb RunAs `
            -ArgumentList "-NoProfile -ExecutionPolicy Bypass -File `"$ScriptPath`""
        exit
    }
}

function Write-Step([string]$Text) {
    Write-Host "  > $Text" -ForegroundColor Cyan
}

function Show-Result([bool]$Ok, [string]$Message) {
    Write-Host ''
    if ($Ok) {
        Write-Host "  [ OK ]  $Message" -ForegroundColor Green
    } else {
        Write-Host "  [ERROR] $Message" -ForegroundColor Red
    }
    Start-Sleep -Seconds $ResultSeconds
}

function Test-TaskInstalled {
    return [bool](Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue)
}

function Stop-Bot {
    # 1) Stop the scheduled task (kills the runner script)
    $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if ($task -and $task.State -eq 'Running') {
        Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    }

    # 2) Kill any runner or bot process started from this folder (including manual runs)
    $procs = Get-CimInstance Win32_Process `
        -Filter "Name='powershell.exe' OR Name='pwsh.exe' OR Name='python.exe' OR Name='pythonw.exe'"
    foreach ($p in $procs) {
        if (-not $p.CommandLine -or $p.ProcessId -eq $PID) { continue }
        $isRunner = $p.CommandLine -like '*run-bot.ps1*' -and $p.CommandLine -like "*$ProjectDir*"
        $isBot    = $p.CommandLine -like '*bot.py*' -and $p.ExecutablePath -and
                    $p.ExecutablePath.StartsWith($ProjectDir, [StringComparison]::OrdinalIgnoreCase)
        if ($isRunner -or $isBot) {
            Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue
        }
    }

    # 3) The real interpreter (child of the venv launcher) is found through bot.pid
    if (Test-Path $PidFile) {
        $botPid = 0
        if ([int]::TryParse((Get-Content $PidFile -Raw).Trim(), [ref]$botPid)) {
            $proc = Get-Process -Id $botPid -ErrorAction SilentlyContinue
            if ($proc -and $proc.ProcessName -like 'python*') {
                Stop-Process -Id $botPid -Force -ErrorAction SilentlyContinue
            }
        }
        Remove-Item $PidFile -Force -ErrorAction SilentlyContinue
    }
    Start-Sleep -Milliseconds 800
}

function Start-Bot {
    if (-not (Test-TaskInstalled)) {
        throw "Service is not installed. Run install-service.cmd first."
    }
    Start-ScheduledTask -TaskName $TaskName
}

function Read-SharedFile([string]$Path, [int]$Tail = 0) {
    # Opens the file even while the bot is writing to it
    try {
        $fs = [IO.File]::Open($Path, 'Open', 'Read', 'ReadWrite')
        $sr = New-Object IO.StreamReader($fs, [Text.Encoding]::UTF8)
        $text = $sr.ReadToEnd()
        $sr.Close()
    } catch {
        return @()
    }
    $lines = $text -split "`r?`n" | Where-Object { $_ -ne '' }
    if ($Tail -gt 0) { $lines = $lines | Select-Object -Last $Tail }
    return @($lines)
}

function Wait-BotStarted([datetime]$Since, [int]$TimeoutSec = 25) {
    # Returns $null on success, otherwise an error message
    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    while ((Get-Date) -lt $deadline) {
        Start-Sleep -Milliseconds 700

        if ((Test-Path $BotLog) -and (Get-Item $BotLog).LastWriteTime -ge $Since) {
            foreach ($line in (Read-SharedFile $BotLog 40)) {
                if ($line -notlike '*Application started*' -or $line.Length -lt 19) { continue }
                try {
                    $time = [datetime]::ParseExact($line.Substring(0, 19), 'yyyy-MM-dd HH:mm:ss', $null)
                    if ($time -ge $Since.AddSeconds(-2)) { return $null }
                } catch { }
            }
        }

        if ((Test-Path $CrashLog) -and (Get-Item $CrashLog).LastWriteTime -ge $Since) {
            $crash = Read-SharedFile $CrashLog
            if ($crash -match 'Traceback') {
                return "Bot crashed:`n" + (($crash | Select-Object -Last 4) -join "`n")
            }
        }
    }
    return "Bot did not start within $TimeoutSec seconds. Check logs\bot.log and logs\crash.log"
}
