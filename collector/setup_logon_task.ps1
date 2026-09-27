# AI Usage Dashboard (optional): run collector.py ONCE at each logon (no periodic repetition).
# Normal updates are event-driven (Claude Code statusLine / Codex notify); this only refreshes after boot.
# (ASCII only on purpose: Windows PowerShell 5.1 reads BOM-less .ps1 files as ANSI.)
# Usage : powershell -NoProfile -ExecutionPolicy Bypass -File .\collector\setup_logon_task.ps1
# Remove: Unregister-ScheduledTask -TaskName "AI Usage Dashboard Logon" -Confirm:$false
param([string]$TaskName = "AI Usage Dashboard Logon")
$ErrorActionPreference = "Stop"
$script = Join-Path $PSScriptRoot "collector.py"
$repo = Split-Path $PSScriptRoot -Parent
$pyw = (Get-Command pythonw.exe -ErrorAction SilentlyContinue | Select-Object -First 1).Source
if (-not $pyw) { $pyw = (Get-Command python.exe -ErrorAction Stop | Select-Object -First 1).Source }
$user = "$env:USERDOMAIN\$env:USERNAME"
$action   = New-ScheduledTaskAction -Execute $pyw -Argument "`"$script`" --reason logon" -WorkingDirectory $repo
$trigger  = New-ScheduledTaskTrigger -AtLogOn -User $user
$trigger.Delay = "PT2M"   # wait 2 minutes after logon (network, etc.)
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
            -ExecutionTimeLimit (New-TimeSpan -Minutes 5) -MultipleInstances IgnoreNew
$principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings -Principal $principal `
  -Description "AI usage dashboard: update data/usage.json once at logon" -Force | Out-Null
Write-Host "Registered: $TaskName (once at logon, 2 min delay). Python: $pyw"
