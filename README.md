# AI使用量ダッシュボード（v1）

Claude Code（Claude Pro）と Codex（ChatGPT Plus）の利用上限の使用状況を、スマホで見やすく表示する静的ページです。

- 公開ページ: https://kimiharu-kitatani.github.io/ai-usage-dashboard/
- 表示: 5時間枠の使用率・リセット時刻（カウントダウン）、週間枠の使用率・リセット時刻、リセット回数、最終取得時刻（30分以上古いと警告）
- 取得できない値は「取得できません」と表示し、推測値は出しません
- 公式ページへのリンク: [Claude](https://claude.ai/settings/usage) / [Codex](https://chatgpt.com/codex/settings/usage)
- 手入力: 各カードの「手入力」から値を入れられます（Claude のリセット回数は手入力のみ）。**その端末のブラウザ（localStorage）にだけ保存**され、公開されません

## 更新のしくみ（定期実行なし・イベント駆動）

常駐や15分ごとの定期実行はしません。PC で AI ツールを使ったときだけ更新します。

| きっかけ | 何が起きるか |
|---|---|
| PC で Claude Code を使う（ステータスライン描画のたび） | `claude_statusline.py` が使用率とリセット時刻をローカルに記録し、ステータスラインに `5h 23% / 週 8%` と表示。前回の更新から **10分以上** 経っていれば `collector.py` を裏で起動（待たない） |
| PC で Codex CLI のターンが終わる（`notify`） | `codex_notify.py` が同じく10分スロットルで `collector.py` を裏で起動 |
| （任意）ログオン時に1回 | `setup_logon_task.ps1` で登録した場合のみ |

`collector.py` は Codex の最新値を `codex app-server` から取得し、Claude はローカル記録を読み、`data/usage.json` だけを commit & push します（commit の作者は GitHub の noreply アドレス）。
GitHub Pages への反映には push 後1〜2分かかります。

注意:
- **スマホでの利用分は、次に PC で Claude Code / Codex を使うまで反映されません**（最終取得時刻と「値の時点」で古さが分かります）
- Claude の値は Claude Code の画面でステータスラインが描画されたときに記録されます。`claude -p`（非対話）では記録されません
- Claude のリセット時刻を過ぎた枠は「取得できません」になります（推測しない）

## 公開される情報 / されない情報

`data/usage.json` は公開リポジトリ・公開ページに載ります。含まれるのは **使用率(%)・リセット時刻・リセット回数・取得元の種類・取得時刻・エラー概要** だけです。
トークン、メールアドレス、アカウントID、会話ログは出力しません。認証情報ファイル（`~/.claude/.credentials.json`、`~/.codex/auth.json` など）は読みません。
Codex の notify に渡される JSON（会話内容を含む）は種別以外読まず、保存もしません。

## データの取得方法

| サービス | 方法 | 備考 |
|---|---|---|
| Codex | `codex app-server`（公式 JSON-RPC）の `account/rateLimits/read` | `windowDurationMins` 300=5時間枠、10080=週間枠。リセット回数は `rateLimitResetCredits.availableCount` |
| Codex（予備） | `~/.codex/sessions/**/rollout-*.jsonl` の `token_count` イベント内 `rate_limits` | app-server が失敗したときだけ |
| Claude Code | statusLine に渡される JSON の `rate_limits.five_hour / seven_day` | Pro/Max で、セッション内の最初の応答後から含まれる |
| Claude のリセット回数 | 自動取得不可 | 手入力 |

> 注: Claude Code v2.1.220 では `claude -p "/usage"` は利用上限ではなくセッションのコスト集計しか出力しないため使いません（`--try-claude-cli` で試せます）。

## セットアップ（Windows PC）

前提: Python 3、Git、GitHub CLI（`gh`）がインストール済みで、git push できること。

```powershell
cd $env:USERPROFILE
gh repo clone kimiharu-kitatani/ai-usage-dashboard
cd ai-usage-dashboard
python collector\collector.py            # 1回取得して push（動作確認）
python collector\install_hooks.py --check # 現在の statusLine / notify を確認
python collector\install_hooks.py        # 未設定なら登録（既存設定は上書きしない・バックアップを作成）
```

- 登録内容
  - `%USERPROFILE%\.claude\settings.json` → `"statusLine": {"type": "command", "command": "python C:/Users/<ユーザー>/ai-usage-dashboard/collector/claude_statusline.py"}`
  - `%USERPROFILE%\.codex\config.toml` → 先頭に `notify = ['<pythonw.exe>', '<clone先>\collector\codex_notify.py']`
- 解除: `python collector\install_hooks.py --uninstall`（このリポジトリが登録した設定だけ外します）
- （任意）ログオン時に1回だけ更新: `powershell -NoProfile -ExecutionPolicy Bypass -File .\collector\setup_logon_task.ps1`
  （解除: `Unregister-ScheduledTask -TaskName "AI Usage Dashboard Logon" -Confirm:$false`）

ログ: `%LOCALAPPDATA%\ai-usage-dashboard\collector.log`
スロットル間隔は環境変数 `AIUSAGE_THROTTLE_MIN`（分、既定 10）で変更できます。

## スマホで使う

公開ページを開き、共有メニューから「ホーム画面に追加」するとアプリのように開けます。

## ファイル構成

- `index.html` … ダッシュボード本体（静的・1ファイル）
- `manifest.webmanifest`, `icons/` … ホーム画面追加用
- `data/usage.json` … collector が更新するデータ
- `collector/collector.py` … 取得・書き出し・commit & push（`data/usage.json` のみ）
- `collector/claude_statusline.py` … Claude Code の statusLine 用
- `collector/codex_notify.py` … Codex の notify 用
- `collector/hooklib.py` … フック共通（10分スロットル・裏で起動）
- `collector/install_hooks.py` … statusLine / notify の登録・確認・解除
- `collector/setup_logon_task.ps1` … （任意）ログオン時1回実行のタスク登録

## usage.json の形式

```json
{
  "schema": 1,
  "fetched_at": "2026-09-28T03:05:05+09:00",
  "services": {
    "codex": {
      "five_hour": { "used_percent": 19.0, "resets_at": "2026-09-28T05:16:32+09:00" },
      "weekly":    { "used_percent": 4.0,  "resets_at": "2026-10-04T15:25:14+09:00" },
      "reset_count": 3,
      "reset_count_expires_at": "2026-10-04T09:51:06+09:00",
      "source": "codex app-server account/rateLimits/read",
      "as_of": "2026-09-28T03:05:05+09:00",
      "error": null
    },
    "claude": { "five_hour": {...}, "weekly": {...}, "reset_count": null, "source": "...", "as_of": "...", "error": "..." }
  }
}
```
