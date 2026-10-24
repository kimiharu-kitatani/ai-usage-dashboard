# Claude Pro usage via statusLine: start a short interactive claude in a trusted Temp folder.
param([int]$TimeoutSec = 120, [switch]$IfUsed)
$ErrorActionPreference = 'Continue'
$state = Join-Path $env:LOCALAPPDATA 'ai-usage-dashboard'
$work  = Join-Path $env:TEMP 'aiusage-claude-ping'  # trusted; no trust prompt
$cachePath = Join-Path $state 'claude_statusline.json'
$log   = Join-Path $state 'collector.log'
$stamp = Join-Path $state 'claude_ping.stamp'
$py    = Join-Path $env:LOCALAPPDATA 'Python\pythoncore-3.14-64\pythonw.exe'
if (-not (Test-Path -LiteralPath $py)) { $py = 'pythonw.exe' }
New-Item -ItemType Directory -Force $state | Out-Null
New-Item -ItemType Directory -Force $work | Out-Null
function L([string]$m) {
  Add-Content -LiteralPath $log -Value ('[{0}] ping: {1}' -f (Get-Date -Format s), $m) -Encoding UTF8
}
if ($IfUsed) {
  # Scheduled: only if Claude Code was used since last ping (mtime of ~/.claude/projects; do not read contents)
  $last = if (Test-Path -LiteralPath $stamp) { (Get-Item -LiteralPath $stamp).LastWriteTime } else { [datetime]::MinValue }
  $proj = Join-Path $env:USERPROFILE '.claude\projects'
  $newest = Get-ChildItem $proj -Recurse -File -Filter *.jsonl -ErrorAction SilentlyContinue |
    Where-Object { $_.FullName -notlike '*aiusage-claude-ping*' } | Sort-Object LastWriteTime | Select-Object -Last 1
  if (-not $newest -or $newest.LastWriteTime -le $last) { exit 0 }
  L 'Claude Code activity detected (scheduled check)'
}
Set-Content -LiteralPath $stamp -Value (Get-Date -Format s) -Encoding UTF8
$before = if (Test-Path -LiteralPath $cachePath) { (Get-Item -LiteralPath $cachePath).LastWriteTimeUtc } else { [datetime]::MinValue }
$claude = (Get-Command claude.cmd -ErrorAction SilentlyContinue).Source
if (-not $claude) { L 'claude.cmd not found'; exit 1 }
# npm shim calls bin\claude.exe; a failed update can leave only .old
$claudeExe = Join-Path $env:APPDATA 'npm\node_modules\@anthropic-ai\claude-code\bin\claude.exe'
if (-not (Test-Path -LiteralPath $claudeExe)) {
  $old = Get-ChildItem (Split-Path $claudeExe) -Filter 'claude.exe.old.*' -ErrorAction SilentlyContinue |
    Sort-Object LastWriteTime -Descending | Select-Object -First 1
  if ($old) {
    Copy-Item -LiteralPath $old.FullName -Destination $claudeExe -Force
    L ('restored claude.exe from {0}' -f $old.Name)
  } else {
    L ('claude.exe missing ({0}). Fix: npm install -g @anthropic-ai/claude-code' -f $claudeExe)
    exit 1
  }
}
function Invoke-ClaudeStatuslinePing {
  param([datetime]$BeforeUtc, [int]$Seconds)
  $env:AIUSAGE_PING = '1'
  $proc = Start-Process -FilePath "$env:WINDIR\System32\conhost.exe" `
    -ArgumentList ("cmd.exe /d /c `"`"{0}`" --model haiku ok`"" -f $claude) `
    -WorkingDirectory $work -WindowStyle Hidden -PassThru
  $ok = $false
  $deadline = (Get-Date).AddSeconds($Seconds)
  while ((Get-Date) -lt $deadline) {
    Start-Sleep -Seconds 3
    if ((Test-Path -LiteralPath $cachePath) -and ((Get-Item -LiteralPath $cachePath).LastWriteTimeUtc -gt $BeforeUtc)) {
      try {
        $j = Get-Content -LiteralPath $cachePath -Raw -Encoding UTF8 | ConvertFrom-Json
        if ($j.rate_limits) { $ok = $true; break }
      } catch {}
    }
  }
  Start-Sleep -Seconds 2
  if ($proc -and -not $proc.HasExited) {
    & taskkill.exe /PID $proc.Id /T /F 2>&1 | Out-Null
  }
  return $ok
}
$ok = Invoke-ClaudeStatuslinePing -BeforeUtc $before -Seconds $TimeoutSec
if (-not $ok) {
  L 'statusline updated=False; retrying once'
  $before2 = if (Test-Path -LiteralPath $cachePath) { (Get-Item -LiteralPath $cachePath).LastWriteTimeUtc } else { [datetime]::MinValue }
  $ok = Invoke-ClaudeStatuslinePing -BeforeUtc $before2 -Seconds ([Math]::Max(60, [int]($TimeoutSec / 2)))
}
L ('statusline updated={0}' -f $ok)
# wait (max 90s) if another collector holds the lock, so fresh statusline is not skipped
$lock = Join-Path $state 'collector.lock'
for ($i = 0; $i -lt 45 -and (Test-Path -LiteralPath $lock); $i++) { Start-Sleep -Seconds 2 }
& $py (Join-Path $PSScriptRoot 'collector.py') --reason claude-ping | Out-Null