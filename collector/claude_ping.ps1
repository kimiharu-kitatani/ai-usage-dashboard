# Claude Pro の利用枠を公式 statusLine 経由で取得するため、隠しコンソールで対話版 claude を1往復だけ起動する
param([int]$TimeoutSec = 120, [switch]$IfUsed)
$state = Join-Path $env:LOCALAPPDATA 'ai-usage-dashboard'
$work  = Join-Path $env:TEMP 'aiusage-claude-ping'  # Temp 配下は信頼済みフォルダ扱いのため確認画面が出ない
$cache = Join-Path $state 'claude_statusline.json'
$log   = Join-Path $state 'collector.log'
$stamp = Join-Path $state 'claude_ping.stamp'
$py    = Join-Path $env:LOCALAPPDATA 'Python\pythoncore-3.14-64\pythonw.exe'
if (-not (Test-Path $py)) { $py = 'pythonw.exe' }
New-Item -ItemType Directory -Force $work | Out-Null
function L($m){ Add-Content -Path $log -Value ("[{0}] ping: {1}" -f (Get-Date -Format s), $m) -Encoding UTF8 }
if ($IfUsed) {
  # 定期実行用: 前回取得以降に Claude Code が使われたか、~/.claude/projects の更新時刻だけで判定（中身は読まない）
  $last = if (Test-Path $stamp) { (Get-Item $stamp).LastWriteTime } else { [datetime]::MinValue }
  $proj = Join-Path $env:USERPROFILE '.claude\projects'
  $newest = Get-ChildItem $proj -Recurse -File -Filter *.jsonl -ErrorAction SilentlyContinue |
    Where-Object { $_.FullName -notlike '*aiusage-claude-ping*' } | Sort-Object LastWriteTime | Select-Object -Last 1
  if (-not $newest -or $newest.LastWriteTime -le $last) { exit 0 }
  L 'Claude Code の利用を検知 (定期チェック)'
}
Set-Content -Path $stamp -Value (Get-Date -Format s)
$before = if (Test-Path $cache) { (Get-Item $cache).LastWriteTimeUtc } else { [datetime]::MinValue }
$claude = (Get-Command claude.cmd -ErrorAction SilentlyContinue).Source
if (-not $claude) { L 'claude.cmd not found'; exit 1 }
$env:AIUSAGE_PING = '1'
$proc = Start-Process -FilePath "$env:WINDIR\System32\conhost.exe" -ArgumentList "cmd.exe /d /c `"`"$claude`" --model haiku ok`"" -WorkingDirectory $work -WindowStyle Hidden -PassThru
$ok = $false
$deadline = (Get-Date).AddSeconds($TimeoutSec)
while ((Get-Date) -lt $deadline) {
  Start-Sleep -Seconds 3
  if ((Test-Path $cache) -and (Get-Item $cache).LastWriteTimeUtc -gt $before) {
    try { $j = Get-Content $cache -Raw -Encoding UTF8 | ConvertFrom-Json; if ($j.rate_limits) { $ok = $true; break } } catch {}
  }
}
Start-Sleep -Seconds 2
& taskkill.exe /PID $proc.Id /T /F 2>&1 | Out-Null
L ("statusline updated={0}" -f $ok)
& $py (Join-Path $PSScriptRoot 'collector.py') --reason claude-ping | Out-Null