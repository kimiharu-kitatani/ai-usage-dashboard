# AI Usage Dashboard: register a Task Scheduler task that runs collector.py every 15 minutes.
# (ASCII only on purpose: Windows PowerShell 5.1 reads BOM-less .ps1 files as ANSI.)
# Usage : powershell -NoProfile -ExecutionPolicy Bypass -File .\collector\setup_task.ps1
# Remove: Unregister-ScheduledTask -TaskName "AI Usage Dashboard Collector" -Confirm:$false
param(
  [int]$IntervalMinutes = 15,
  [string]$TaskName = "AI Usage Dashboard Collector"
)
$ErrorActionPreference = "Stop"
$script = Join-Path $PSScriptRoot "collector.py"
$repo = Split-Path $PSScriptRoot -Parent
$pyw = (Get-Command pythonw.exe -ErrorAction SilentlyContinue | Select-Object -First 1).Source
if (-not $pyw) { $pyw = (Get-Command python.exe -ErrorAction Stop | Select-Object -First 1).Source }
Write-Host "Python : $pyw"
Write-Host "Script : $script"
Write-Host "Repo   : $repo"

$action   = New-ScheduledTaskAction -Execute $pyw -Argument "`"$script`"" -WorkingDirectory $repo
$trigger  = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes $IntervalMinutes)
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
            -ExecutionTimeLimit (New-TimeSpan -Minutes 10) -MultipleInstances IgnoreNew
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings -Principal $principal `
  -Description "AI usage dashboard: write data/usage.json and push to GitHub" -Force | Out-Null
Write-Host "Registered: $TaskName (every $IntervalMinutes min, only while logged on)"
