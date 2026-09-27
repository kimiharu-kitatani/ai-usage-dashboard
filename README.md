# AI使用量ダッシュボード（v1）

Claude Code（Claude Pro）と Codex（ChatGPT Plus）の利用上限の使用状況を、スマホで見やすく表示する静的ページです。
GitHub Pages で公開し、Windows PC 上のコレクターが `data/usage.json` を約15分ごとに更新・push します。

- 表示: 5時間枠の使用率・リセット時刻（カウントダウン）、週間枠の使用率・リセット時刻、リセット回数、最終取得時刻（30分以上古いと警告）
- 取得できない値は「取得できません」と表示し、推測値は出しません
- 公式ページへのリンク: [Claude](https://claude.ai/settings/usage) / [Codex](https://chatgpt.com/codex/settings/usage)
- 手入力: 各サービスのカードの「手入力」から値を入れられます（Claude のリセット回数は手入力のみ）。**その端末のブラウザ（localStorage）にだけ保存**され、公開されません

## 公開される情報 / されない情報

`data/usage.json` は公開リポジトリ・公開ページに載ります。含まれるのは **使用率(%)・リセット時刻・リセット回数・取得元の種類・取得時刻・エラー概要** だけです。
トークン、メールアドレス、アカウントID、会話ログは出力しません。コレクターは認証情報ファイル（`~/.claude/.credentials.json`、`~/.codex/auth.json` など）を読みません。

## データの取得方法

| サービス | 方法 | 備考 |
|---|---|---|
| Codex | `codex app-server`（公式 JSON-RPC）の `account/rateLimits/read` | PC で実行するたびに最新値を取得。`windowDurationMins` が 300=5時間枠、10080=週間枠。リセット回数は `rateLimitResetCredits.availableCount` |
| Codex（予備） | `~/.codex/sessions/**/rollout-*.jsonl` の `token_count` イベント内 `rate_limits` | app-server が失敗したときだけ。最後に Codex を使った時点の値 |
| Claude Code | Claude Code の **statusLine** に渡される JSON の `rate_limits.five_hour / seven_day` | `collector/claude_statusline.py` がローカルにキャッシュし、コレクターがそれを読む。**PC で Claude Code を使ったときにだけ更新**されます（スマホでの利用分は、次に PC で Claude Code を使うまで反映されません）。リセット時刻を過ぎた枠は「取得できません」になります |
| Claude のリセット回数 | 自動取得不可 | 手入力 |

> 注: Claude Code v2.1.220 では `claude -p "/usage"` は利用上限ではなくセッションのコスト集計しか出力しないため、既定では使いません（`--try-claude-cli` で試せます）。

## セットアップ（Windows PC）

前提: Python 3、Git、GitHub CLI（`gh`）がインストール済みで、`gh auth login` 済み。

1. リポジトリを clone（場所は任意。以下は例）
   ```powershell
   cd $env:USERPROFILE
   gh repo clone kimiharu-kitatani/ai-usage-dashboard
   cd ai-usage-dashboard
   gh auth setup-git   # git push に gh の認証を使う（未設定の場合）
   ```
2. 手動でテスト実行（push しない）
   ```powershell
   python collector\collector.py --no-push --debug --out $env:TEMP\usage-test.json
   Get-Content $env:TEMP\usage-test.json
   ```
3. Claude の自動取得を有効にする（任意・推奨）: `%USERPROFILE%\.claude\settings.json` に以下を追加（パスは clone した場所に合わせ、`/` 区切りで書く）。既に `statusLine` を設定している場合は置き換えになるので注意。
   ```json
   {
     "statusLine": {
       "type": "command",
       "command": "python C:/Users/<ユーザー名>/ai-usage-dashboard/collector/claude_statusline.py"
     }
   }
   ```
   その後 PC で Claude Code を起動して1回やり取りすると、`%LOCALAPPDATA%\ai-usage-dashboard\claude_statusline.json` に使用率とリセット時刻だけが保存され、ステータスラインに `[モデル] | 5h: xx% 7d: yy%` と表示されます。
4. push まで含めて1回実行
   ```powershell
   python collector\collector.py
   ```
5. 15分ごとの自動実行を登録（タスク スケジューラ）
   ```powershell
   powershell -NoProfile -ExecutionPolicy Bypass -File .\collector\setup_task.ps1
   ```
   - ログオン中のみ実行されます（PC がスリープ/シャットダウン中は更新されず、ページに「古い」警告が出ます）
   - 削除: `Unregister-ScheduledTask -TaskName "AI Usage Dashboard Collector" -Confirm:$false`
   - 手動で作る場合: 操作=`pythonw.exe`、引数=`"<clone先>\collector\collector.py"`、開始=`<clone先>`、トリガー=15分ごとに繰り返し

ログ: `%LOCALAPPDATA%\ai-usage-dashboard\collector.log`

## スマホで使う

GitHub Pages の URL を開き、共有メニューから「ホーム画面に追加」するとアプリのように開けます。

## ファイル構成

- `index.html` … ダッシュボード本体（静的・1ファイル）
- `manifest.webmanifest`, `icons/` … ホーム画面追加用
- `data/usage.json` … コレクターが更新するデータ
- `collector/collector.py` … 取得・書き出し・commit & push（`data/usage.json` のみ）
- `collector/claude_statusline.py` … Claude Code の statusLine 用（rate_limits をローカル保存）
- `collector/setup_task.ps1` … タスク スケジューラ登録スクリプト

## usage.json の形式

```json
{
  "schema": 1,
  "fetched_at": "2026-09-28T03:00:19+09:00",
  "services": {
    "codex": {
      "five_hour": { "used_percent": 19.0, "resets_at": "2026-09-28T05:16:32+09:00" },
      "weekly":    { "used_percent": 4.0,  "resets_at": "2026-10-04T15:25:14+09:00" },
      "reset_count": 3,
      "reset_count_expires_at": "2026-10-04T09:51:06+09:00",
      "source": "codex app-server account/rateLimits/read",
      "as_of": "2026-09-28T03:00:19+09:00",
      "error": null
    },
    "claude": { "five_hour": {...}, "weekly": {...}, "reset_count": null, "source": "...", "as_of": "...", "error": "..." }
  }
}
```
