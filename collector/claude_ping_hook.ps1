# Claude Code の Stop フックから呼ばれる。すぐ戻り、裏で claude_ping.ps1 を起動する
if ($env:AIUSAGE_PING -eq '1') { exit 0 }
$state = Join-Path $env:LOCALAPPDATA 'ai-usage-dashboard'
New-Item -ItemType Directory -Force $state | Out-Null
Add-Content -Path (Join-Path $state 'collector.log') -Value ("[{0}] claude Stop hook fired" -f (Get-Date -Format s)) -Encoding UTF8
$stamp = Join-Path $state 'claude_ping.stamp'
if ((Test-Path $stamp) -and ((Get-Date) - (Get-Item $stamp).LastWriteTime).TotalMinutes -lt 10) { exit 0 }
Start-Process -FilePath powershell.exe -ArgumentList '-NoProfile','-ExecutionPolicy','Bypass','-WindowStyle','Hidden','-File',"`"$PSScriptRoot\claude_ping.ps1`"" -WindowStyle Hidden
exit 0