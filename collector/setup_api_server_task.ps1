# AI Usage Dashboard: register independent API server + relay poller watchdog task
# Also ensures LaunchAIWidget runs at logon with no 72h execution limit.
param([string]$TaskName = "AIUsage_APIServer")
$ErrorActionPreference = "Stop"

$script = Join-Path $PSScriptRoot "api_server.py"
$repo = Split-Path $PSScriptRoot -Parent
$pyw = "C:\Users\kimih\AppData\Local\Programs\Python\Python312\pythonw.exe"
if (-not (Test-Path -LiteralPath $pyw)) {
    $pyw = (Get-Command pythonw.exe -ErrorAction SilentlyContinue | Select-Object -First 1).Source
}
if (-not $pyw) {
    $pyw = (Get-Command python.exe -ErrorAction Stop | Select-Object -First 1).Source
}

$user = "$env:USERDOMAIN\$env:USERNAME"
$action = New-ScheduledTaskAction -Execute $pyw -Argument "`"$script`"" -WorkingDirectory $repo

# Trigger 1: At logon (5s delay)
$tLogon = New-ScheduledTaskTrigger -AtLogOn -User $user
$tLogon.Delay = "PT5S"

# Trigger 2: Every 5 minutes watchdog (api_server.py exits immediately if port 8768 is already healthy)
$tWatch = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes 5)

$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Seconds 0) `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -MultipleInstances IgnoreNew

$principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger @($tLogon, $tWatch) -Settings $settings -Principal $principal `
    -Description "AI usage dashboard: background API server (127.0.0.1:8768) and Xserver relay poller with 5-min watchdog" -Force | Out-Null

# Also ensure LaunchAIWidget has AtLogOn trigger and no 72h timeout
$wTask = Get-ScheduledTask -TaskName "LaunchAIWidget" -ErrorAction SilentlyContinue
if ($wTask) {
    $wLogon = New-ScheduledTaskTrigger -AtLogOn -User $user
    $wLogon.Delay = "PT10S"
    $wSettings = New-ScheduledTaskSettingsSet `
        -AllowStartIfOnBatteries `
        -DontStopIfGoingOnBatteries `
        -StartWhenAvailable `
        -ExecutionTimeLimit (New-TimeSpan -Seconds 0) `
        -RestartCount 3 `
        -RestartInterval (New-TimeSpan -Minutes 1) `
        -MultipleInstances IgnoreNew
    Set-ScheduledTask -TaskName "LaunchAIWidget" -Trigger $wLogon -Settings $wSettings | Out-Null
}

Write-Host "Registered: $TaskName (AtLogOn + 5-min watchdog, unlimited runtime). Python: $pyw"
