# 貼り付け用プロンプト（AI利用枠ダッシュボード改修）

他エージェント（Codex / Claude Code / Antigravity / Cursor 等）に、そのまま貼って作業を開始させるための文面です。

---

あなたは北谷さん（Kimiharu Kitatani）のPC上の「AI利用枠ダッシュボード」を改修する担当です。作業前に次を必ず読んでください。

1. `C:\Kita\AI_CUSTOM_INSTRUCTIONS.md`（共通ルール。日本語・結論先・推測しない・不可逆操作の前確認・利用枠ペース・Git運用）
2. 引き継ぎ資料: `C:\Users\kimih\ai-usage-dashboard\docs\HANDOFF.md`  
   （同じ内容のコピー: `C:\Kita\project\ai-usage-dashboard\HANDOFF.md` / リポジトリ内 `docs/HANDOFF.md`）

## このプロジェクトの要約

- リポジトリ: `kimiharu-kitatani/ai-usage-dashboard`（GitHub Pages）
- PCクローン: `C:\Users\kimih\ai-usage-dashboard`
- 公開ページ: https://kimiharu-kitatani.github.io/ai-usage-dashboard/
- PCウィジェット: `desktop\widget.pyw` を **pythonw** で起動（タスク名 `LaunchAIWidget`）
- データ: Codex は `codex app-server` の `account/rateLimits/read`、Claude は statusLine の `rate_limits`、公開は `data/usage.json`
- 表示: Claude/Codex 各ウィンドウに 5h・今日・1w の3本バー（詳細は HANDOFF.md §5）
- 過去に `CODEX_5H_ONLY_FROM` で Codex を5hのみ表示する切替があったが、現行コードからは除去済み。再導入はバックアップ参照（HANDOFF.md §5.4）

## 絶対守ること

- 取れない値は推測せず「不明」/ null
- `.credentials.json` / `auth.json` 等の認証ファイルを読まない・出さない
- 非公開APIを使わない
- 設定・表示ロジック変更前に `.bak-YYYYMMDDHHMM` を残す
- 未コミットの無関係差分を巻き込まない（現状 widget/index/collector に別作業の差分があり得る）
- 常駐や高頻度の定期実行を増やさない
- HTMLを勝手に自動で開かない（URLを渡す）
- 有料プランの勝手な契約をしない
- 北谷さんへの返答は日本語・短く・結論先

## 作業の進め方

1. 依頼内容を自分の言葉で短く要約し、計画を出してから実装する（いきなり大きな変更をしない）
2. 実ファイルを読んで現状を確認する（HANDOFF とコードが食い違う場合はコード優先、差は報告）
3. 変更→ローカル確認→関係ファイルだけ commit → push（指示があるとき）
4. 完了報告に変更点・影響範囲・確認方法・Pages URL（該当時）を書く

## いまの依頼

（ここに具体的な改修内容を書く）

