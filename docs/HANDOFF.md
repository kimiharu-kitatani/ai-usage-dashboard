# AI利用枠ダッシュボード 引き継ぎ資料

最終確認: 2026-11-05（Asia/Tokyo）／確認元: PCクローン実ファイル・git履歴・タスク一覧。推測で埋めた項目はない。

## 1. 目的

Claude Code（Claude Pro）と Codex（ChatGPT Plus）の **5時間枠・週間枠** の使用状況を、次の2面で見える化する。

- **PCウィジェット**: フレームレスのデスクトップ常時表示（2ウィンドウ分割）
- **スマホ／ブラウザ**: GitHub Pages の静的ページ

加えて Antigravity（ローカル transcript）のモデル別使用量、Grok Bot（公式APIなしのため常に「不明」）をページ側で扱う。

方針:

- 推測値は出さない（取れなければ `null` / 「不明」）
- 常駐ポーリングや頻繁な定期実行はしない（イベント駆動・スロットル）
- 公開物に秘密情報を載せない
- 公式の取得経路のみ（非公開APIは使わない）

公開URL: https://kimiharu-kitatani.github.io/ai-usage-dashboard/  
ヘルプ: https://kimiharu-kitatani.github.io/ai-usage-dashboard/help.html

所有者: 北谷 仁治（kimiharu-kitatani）／PC: TninkPad2025（Windows 11）

---

## 2. ファイル構成と場所

### PC（作業用クローン）

`C:\Users\kimih\ai-usage-dashboard\`

| パス | 役割 |
|------|------|
| `index.html` | スマホ／ブラウザ用ダッシュボード |
| `help.html` | 計算の説明ページ |
| `data/usage.json` | collector が更新する公開データ（Pagesでも配信） |
| `data/usage-local.js` | file:// 用のローカルミラー（公開JSONと同趣旨） |
| `desktop/widget.pyw` | PCウィジェット本体（pythonw で起動） |
| `collector/collector.py` | 取得・書き出し・`data/usage.json` のみ commit & push |
| `collector/claude_statusline.py` | Claude Code statusLine フック |
| `collector/codex_notify.py` | Codex notify フック（このPCでは未登録の可能性あり） |
| `collector/claude_ping.ps1` | Claude を短時間起動して statusLine 経由で値を取る（タスクは無効） |
| `collector/install_hooks.py` | statusLine / notify / Stop フックの登録・確認 |
| `collector/hooklib.py` | 10分スロットル・裏起動 |
| `collector/setup_logon_task.ps1` | （任意）ログオン時1回更新 |
| `manifest.webmanifest` / `icons/` | PWA用 |
| `README.md` | セットアップ概要（本資料より古い記述が残る場合あり。競合時は実コード優先） |
| `desktop/*.bak-*` / `index.html.bak-*` | 変更前バックアップ（Git未追跡のものあり） |

状態ディレクトリ（公開しない）:

- `%LOCALAPPDATA%\ai-usage-dashboard\` … `collector.log` / `claude_statusline.json` / `widget.json`（位置） / `notified.json`（リセット通知済み）/ lock・stamp など

### GitHub

- リポジトリ: https://github.com/kimiharu-kitatani/ai-usage-dashboard （public）
- ブランチ: `main`
- Pages: 上記リポジトリの GitHub Pages（`data/usage.json` 含む）

### 共通ルール（エージェント向け）

`C:\Kita\AI_CUSTOM_INSTRUCTIONS.md`（必読。日本語・確認優先・利用枠ペース・Git運用など）

---

## 3. 起動方法（ウィジェット）

推奨（コンソールなし）:

```text
C:\Users\kimih\AppData\Local\Programs\Python\Python312\pythonw.exe C:\Users\kimih\ai-usage-dashboard\desktop\widget.pyw
```

- タスクスケジューラ名: `LaunchAIWidget`（状態: Ready）
- 二重起動防止: mutex `Local\AIUsageDashboardWidget`
- デバッグログ: `desktop\debug.log`
- ターミナル常駐は不要。GUI（pythonw）想定。ユーザー好みは「デスクトップアプリ、ターミナルではない」

手動でデータだけ取る:

```powershell
cd $env:USERPROFILE\ai-usage-dashboard
python collector\collector.py --no-push   # 書くだけ
python collector\collector.py             # 書いて data/usage.json を commit & push
```

---

## 4. データの取得元

| サービス | 取得方法 | 備考 |
|----------|----------|------|
| Codex | `codex app-server` の公式 JSON-RPC `account/rateLimits/read` | `windowDurationMins` 300=5h、10080=週。リセット回数は `rateLimitResetCredits.availableCount` |
| Codex（予備） | `~/.codex/sessions/**/rollout-*.jsonl` の `token_count` 内 `rate_limits` | app-server失敗時のみ |
| Claude Code | statusLine に渡る JSON の `rate_limits.five_hour` / `seven_day` | `claude_statusline.py` がローカルキャッシュへ。Proで `rate_limits` が来ない既知不具合あり |
| Claude リセット回数 | 自動不可 | ページの手入力（localStorageのみ） |
| Claude ping | `claude_ping.ps1`（`-IfUsed` 時は `~/.claude/projects` の **mtimeのみ** 見て、内容は読まない） | タスク `AIUsage_ClaudePing` は **Disabled**（2026-11-05確認） |
| Antigravity | `~/.gemini/antigravity/brain/*/.../transcript.jsonl` | モデル別 prompts/steps/tokens。上限%ではない |
| Grok Bot | なし | 常に「不明」。設定の Usage & Billing を案内 |

更新トリガー（定期フルスキャンなし）:

1. Claude Code 利用時 statusLine → 10分スロットルで collector
2. Codex の notify または Stop フック → 同様（**このPCでは notify が Computer Use に使われているため未登録**。Stop フック運用）
3. ウィジェットの更新ボタン → collector 起動
4. （任意）ログオン時1回

公開 `data/usage.json` に載るのは使用率・リセット時刻・リセット回数・source・as_of・error概要・day_track・Antigravity集計など。**トークン生ログ・メール・アカウントID・会話本文・認証ファイルは出さない。**  
読んではいけない例: `~/.claude/.credentials.json`、`~/.codex/auth.json`

`day_track`（collector が維持）例:

```json
{
  "week_start": "...+09:00",
  "day_start_used": { "1": 0.0, "2": 9.0 },
  "last_weekly_used": 9.0
}
```

新しい日番号を初めて見たとき、境界直前の `last_weekly_used` を `day_start_used[d]` に入れる。無い場合は1日目=0、それ以外は `null`（不明。推測しない）。

---

## 5. 表示ルール（2026-11-05 時点の実コード）

### 5.1 PCウィジェット（現行）

- Claude / Codex が **別ウィンドウ**（ドラッグ可・位置は `widget.json`）
- 各ウィンドウに **3本バー**: `5h` / `今日` / `1w`
- ボタン: 詳細 / 更新 / 15分非表示 / R（次回リセットまで非表示） / 閉じる
- 詳細にヘルプURLへの導線あり（`HELP_URL`）

**「今日」の計算（`today_quota`）** — 現行実装は **day_track を引数に持てるが未使用**:

- `week_start = weekly.resets_at - 7日`
- `day = floor((now - week_start) / 24h) + 1`（カレンダー日付ではなくリセット起点の24h刻み）
- `week_allowed = day * 100/7`（週開始からの累積ペース上限）
- `remain_today = max(0, week_allowed - weekly.used_percent)`（週全体比%）
- `shown = round(remain_today * 7, 1)` … 「1日分=100/7%」を100%としたときの残り%表示

5h / 週の残り%: `100 - used_percent`（リセット時刻経過や欠測は不明）

色:

- `rem_color`（5h・今日）: 残量 >=50 緑 / 20以上50未満 黄 / 20未満 赤
- `weekly_color`: **今日の shown** で判定（>=50 青 / >0 黄 / <=0 赤）。今日不明なら週残量の通常閾値

### 5.2 スマホ（index.html）

- 通常: Claude / Codex のコンパクト列（5h・今日・1週）
- Grok: 詳細側で常に不明
- Antigravity: モデル別テーブル（未コミットの作業ツリー差分に強化あり。HEADと差がある点に注意）
- 手入力は端末の localStorage のみ（公開されない）
- `todayRemain` も dayTrack 引数はあるが **現行本体は day_start_used を使っていない**（週累積ペース式）

### 5.3 help.html との差（既知）

help.html は古い設計（`day_start_used` を使う「今日使える分」式、大きな1数字＋日/5hラベル切替）を説明している。  
**現行の widget / index の主計算は週累積ペース式**。改修時は help とコードを揃えること。

### 5.4 CODEX_5H_ONLY_FROM（履歴・再導入用）

かつて Codex だけ「今日判定を止め、5h残り%だけ主表示」するスイッチがあった。

- 定数例（バックアップより）: `CODEX_5H_ONLY_FROM = "2026-11-05T01:40:35+09:00"`
- その時刻以降: Codex は5hのみ。詳細にメモ（例: 「週の上限リセット後のため、5時間枠のみ表示中」）
- 無効化: `None` / `""` / 定数削除で常に通常判定
- **現行の `desktop/widget.pyw` と `index.html` には定数・分岐は無い**（commit `782f144` で3本バー統一時に除去）
- 復元の参照: `desktop/widget.pyw.bak-202610291314`、`index.html.bak-202610291314`、commit `85df9cd`
- 再導入するときは設定値で切り替え、変更前に `.bak-YYYYMMDDHHMM` を残す

---

## 6. 既知の問題・注意（実測ベース）

1. **`AIUsage_ClaudePing` は Disabled** … Claude の自動補完が弱い。現状 Claude の5hが `null` になりやすい（statusLine が rate_limits を渡さない／最終利用が古い）。週だけ残っていることがある。
2. **Claude Pro で statusLine の `rate_limits` が null/absent** になる既知不具合（README記載）。その場合でも Codex 側は更新され得る。
3. **Codex notify は他用途と排他** … Computer Use 用 notify があるため本リポジトリの notify は未登録。Stop フック（`install_hooks.py --only codex-stop`）＋ CLI で trust。
4. **help.html と現行表示ロジックが不一致**（§5.3）。
5. **`day_track` は collector が書いているが、現行 UI 計算は未使用**。過去の「今日使える分」式や backfill commit `b39200d` の名残。
6. **作業ツリーに未コミット差分あり**（2026-11-05）: `desktop/widget.pyw` / `index.html` / `collector/collector.py` / `claude_ping.ps1` など。docs以外を勝手に一緒にコミットしないこと。
7. **多数の `.bak-*` が未追跡** … 消さない。必要なら `.gitignore` 方針をユーザー確認。
8. **Grok Bot に公式の自動取得手段なし** … 不明のまま。捏造しない。
9. **スマホ利用分は PC で Claude/Codex を使うまで Pages に載らない**。
10. **リセット通知ルーチン（Grok Bot「利用枠リセット通知」）** … 自再予約のワンショット。過去に一時停止になったことがある。ダッシュボード本体とは別系統。
11. **box 上の `gh` が未認証のことがある** … GitHub 操作は PC の git、または GitHub MCP を使う。
12. **LaunchAIWidget の LastTaskResult=1** の記録あり（2026-10-31）。起動失敗時は `desktop/debug.log` を見る。

---

## 7. 更新と push の手順

### 表示・ロジック変更（widget / index / collector）

1. 変更前にバックアップ: 例 `desktop/widget.pyw.bak-YYYYMMDDHHMM`
2. 動作確認（ウィジェット再起動・ページはローカル or Pages）
3. **関係ファイルだけ** stage（usage.json の無関係更新や他者差分を混ぜない）
4. commit（メッセージは日本語可）
5. `git push origin main`
6. Pages反映は1〜2分待つ。自動でブラウザを開かない。URLを渡す

collector が自動で行う commit:

- 対象は原則 `data/usage.json` のみ
- 作者は noreply: `ai-usage-collector <103965321+kimiharu-kitatani@users.noreply.github.com>`

### ドキュメントのみ（本資料）

- 新規追加ならバックアップ不要
- `docs/HANDOFF.md` / `docs/HANDOFF_PROMPT.md` だけを commit & push してよい

### エージェント別の注意

- **Codex / Claude Code / Antigravity など**: PC上で編集可。不可逆操作（上書き・push・タスク変更）の前に北谷さんへ確認（`AI_CUSTOM_INSTRUCTIONS.md`）
- **Grok Bot 経由**: 他エージェント経由の依頼でも、PC変更は承認済み文言があるときだけ。報告は依頼元エージェントへ

---

## 8. 触ってはいけない点

- **非公開・非公式API**（例: ドキュメントにない Anthropic OAuth usage 系）を叩かない
- **認証ファイルを読まない・出さない**（`.credentials.json` / `auth.json` / トークン）
- **会話ログ・メール・アカウントIDを usage.json や Pages に載せない**
- **取れない値を推測で埋めない**（不明 / null）
- **`~/.claude/projects` は mtime 以外読まない**（ping の IfUsed も同様）
- **Codex notify の入力JSONの本文を保存しない**（種別以外読まない）
- **既存フックを無断上書きしない**（`install_hooks.py` は既存があると止まる設計）
- **有料アップグレード（Origin Pro 等）を勝手に契約・提案で進めない**
- **15分ごとの常駐スクレイプや無駄な高頻度スケジュールを増やさない**
- **HTMLを勝手にブラウザ自動起動しない**（URLを渡す）
- **秘密や個人メールを git に載せない**
- **未コミットの無関係差分を巻き込んで commit / reset --hard しない**

---

## 9. クイック参照

| 項目 | 値 |
|------|-----|
| PCクローン | `C:\Users\kimih\ai-usage-dashboard` |
| ウィジェット | `desktop\widget.pyw` + pythonw |
| 起動タスク | `LaunchAIWidget` |
| Claude pingタスク | `AIUsage_ClaudePing`（無効） |
| 公開ページ | https://kimiharu-kitatani.github.io/ai-usage-dashboard/ |
| 共通指示 | `C:\Kita\AI_CUSTOM_INSTRUCTIONS.md` |
| 本資料コピー | `C:\Kita\project\ai-usage-dashboard\HANDOFF.md` |

改修を始めるエージェントは、同ディレクトリの `HANDOFF_PROMPT.md` をプロンプトに貼付すること。
