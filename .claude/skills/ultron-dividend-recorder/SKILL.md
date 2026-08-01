---
name: ultron-dividend-recorder
description: >-
  個別株の配当金計算書・支払通知書の画像から配当実績を抽出し、配当台帳(dividend-data/records.json、git 管理外)へ記録する。画像を読み取って銘柄名・一株あたり配当・株数・配当金額・基準日を抽出し、ユーザー確認のうえ決定論スクリプト経由で追記する(検算・重複検出・スキーマ検証はスクリプトが行い、合計を暗算しない)。personal-dashboard は環境変数 DIVIDEND_RECORDS_PATH でこの台帳を参照する。過去記録の CSV 一括移行にも対応。「この配当金計算書を記録して」「今年の配当いくら？」「配当の累計を見せて」等の明示依頼時のみ起動(自動起動しない)。投資助言ではない。銘柄の選定は ultron-high-dividend-stock-screener の領分。
model: sonnet
effort: low
metadata:
  type: skill
  data_dir: <repo>/dividend-data
---

# dividend-recorder — 配当実績の画像取り込み・記録スキル

配当金計算書・支払通知書の**画像**から配当実績を抽出し、リポジトリ直下の配当台帳
（`dividend-data/records.json`）へ記録する。personal-dashboard は環境変数
`DIVIDEND_RECORDS_PATH` でこの台帳を参照し、配当メトリクス
（累計配当金額 / 年間配当合計）の表・メトリクスとして可視化する。

> **スタンス（毎回明示）**: 本スキルの出力は**配当実績の機械的な記録・集計であって、投資助言ではない**。
> 画像から読み取った数値は誤読の可能性があるため、**必ずユーザー確認を挟んでから**記録する。

## 棲み分け

- **`ultron-high-dividend-stock-screener`**: これから買う銘柄の選定・スクリーニング（未来）。
- **本スキル**: 受け取った配当の実績記録と集計（過去の事実）。
- 配当は個人資産だが家計簿ではないため、家計系スキル（`ultron-family-budget-manager`）のエントリには混ぜない。

## 場所（コードとデータは分離されている）

- ツール本体・参照資料: このスキルディレクトリ `.claude/skills/ultron-dividend-recorder/`
- **データ**: スキルが属する git リポジトリ直下の `dividend-data/`（**git 管理外**。`.gitignore` 済み）。
  `projects/` 配下のプロジェクトには依存しない（スキル群はリポジトリ内で自己完結させる）。
  ```
  dividend-data/
  ├── records.json        # 配当台帳（DividendRecord[]。唯一の真実）
  └── inbox/              # 取り込み待ちの画像を置く場所
      └── processed/      # 記録済み画像の退避先（二重取り込み防止・一次資料の保全）
  ```
- records.json のパスは環境変数 `DIVIDEND_RECORDS_PATH` または `--records` で上書き可。
- `dividend-data/` は Google Drive（`マイドライブ/onclimb-industries/配当/`）への
  シンボリックリンクになっている場合がある（2026-07-14 設定。ultron-family-budget-manager の
  `references/google-drive-sync-setup.md` と同じ方式・単一ライター運用）。パスは変わらないので
  スキル・スクリプトはそのまま動く。リンク先が読めない場合は Drive for Desktop の起動を確認する。
- レコード形式は personal-dashboard 側 `shared/dividends.ts` の `DividendRecord` と**完全一致**させる
  （camelCase・7 フィールド。勝手にフィールドを増やさない）:
  `{ stockName, dividendPerShare, shares, amount, recordDate, sourceImage, extractedAt }`
- **ダッシュボード連携**: personal-dashboard は `.env` の `DIVIDEND_RECORDS_PATH` に
  この台帳の絶対パスを設定して読む（未設定時は dashboard 内の既定パスにフォールバック）。
  台帳スキーマを変える場合は dashboard 側と同時に変える必要がある。

## 役割分担（重要）

- **Claude（あなた）**: 画像の読み取りと 5 項目の抽出、`references/reading-guide.md` に基づく
  項目の対応付け、ユーザーへの確認対話、スクリプト出力の転記。
- **スクリプト（決定論）**: `scripts/dividend.py` が スキーマ検証・検算（一株配当 × 株数 ≒ 配当金額）・
  重複検出・追記・年別/銘柄別集計 をすべて行う。

→ **Claude は合計・検算を暗算しない**。集計値は必ず `dividend.py summary` の出力を転記する。
→ 読み取った値は**ユーザー確認を経てから** `add` する（確認前に書き込まない）。

## フロー A: 画像からの記録（メイン）

1. **画像の受け取り**: ユーザーが画像パスを指定（`dividend-data/inbox/` に置かれている場合も含む。
   リポジトリ内の別の場所にあれば inbox へ移動してから処理する）。複数枚あれば 1 枚ずつ処理する。
2. **読み取り**: Read ツールで画像を読み、`references/reading-guide.md` の対応表に従って抽出する:
   - `stockName` 銘柄名（会社名。証券コードが読めれば「銘柄名(コード)」も可）
   - `dividendPerShare` 一株あたり配当金（円）
   - `shares` 所有株数
   - `amount` **配当金額（税引前）**。「支払金額（税引後）」ではない点に注意
   - `recordDate` 基準日。**無ければ支払確定日で代用**し、その旨をユーザーに伝える
   - 読み取れない数値は 0、文字列は空にし、「要確認」として明示する
3. **確認**: 抽出結果を表で提示し、検算（一株配当 × 株数）の一致/不一致も添えて確認を取る。
4. **追記**: 承認後にスクリプトで追記する。
   ```bash
   python3 .claude/skills/ultron-dividend-recorder/scripts/dividend.py add \
     --stock-name "<銘柄名>" --dividend-per-share <円> --shares <株数> \
     --amount <円> --record-date <YYYY-MM-DD> --source-image "<画像ファイル名>"
   ```
   - `DUPLICATE` が出たら勝手に `--force` しない。既存レコードを見せて意図を確認する。
5. **画像の退避**: 記録済み画像を `dividend-data/inbox/processed/` へ移動する
   （未処理と記録済みを区別し、再実行時の二重取り込みを防ぐ。**画像は削除しない**＝一次資料の保全）。
6. **ダッシュボード反映（任意）**: ユーザーが表示更新まで求めたら dashboard 側で収集を回す
   （dashboard の `.env` に `DIVIDEND_RECORDS_PATH` が設定されている前提）。
   ```bash
   cd projects/personal-dashboard && vp run collect
   ```
   collector は独立実行なので他指標（GitHub 系）が失敗しても配当は反映される。
   反映しなくても台帳には記録済みで、次回の定期収集で拾われる旨を伝える。
   dashboard が手元に無い環境では台帳の記録までで完了とする。
7. **通知**: 記録した件数・金額と、`summary` の最新集計を一言で報告する。

## フロー B: 集計・一覧の確認

「今年の配当いくら？」「配当の累計は？」等に対し、スクリプト出力を転記して答える。

```bash
python3 .claude/skills/ultron-dividend-recorder/scripts/dividend.py summary --year 2026
python3 .claude/skills/ultron-dividend-recorder/scripts/dividend.py list --year 2026 --stock "<銘柄>"
```

ダッシュボード上の表示（累計配当金額 / 年間配当合計ウィジェット）は最終スナップショット時点の
値なので、台帳と差があれば `vp run collect` を案内する。

## フロー C: Notion 過去データの一括移行（初回のみ）

Notion で手打ち管理していた過去の配当記録を台帳へ移す。

1. **データの受け取り**: 次のいずれかでデータをもらう。
   - Notion の DB を CSV エクスポート（Notion: データベース右上 `…` → Export → CSV）
   - チャットに表を貼り付け / Notion MCP・ブラウザで読み取り（接続できる場合）
2. **整形**: Claude が `stockName, recordDate, amount [, dividendPerShare, shares]` の CSV に整形する
   （列対応は `references/reading-guide.md` の移行マッピング参照。不明な数値は 0）。
   整形結果はスクラッチパッドに保存し、**変換の対応関係をユーザーに提示して確認を取る**。
3. **ドライラン → 本実行**:
   ```bash
   python3 .claude/skills/ultron-dividend-recorder/scripts/dividend.py import-csv --file <csv> --dry-run
   python3 .claude/skills/ultron-dividend-recorder/scripts/dividend.py import-csv --file <csv>
   ```
   - `sourceImage` は既定で `notion-migration` が入る（出所の記録）。
   - 重複はスクリプトが自動スキップするので、移行のやり直しで二重記録にはならない。
4. **検証**: `check` と `summary` を実行し、Notion 側の合計と一致するかユーザーと突き合わせる。

## 品質・安全性

- 画像には住所・氏名・株主番号等の個人情報が写り込む。**台帳に転記するのは定義済み 7 フィールドのみ**
  とし、住所・株主番号・口座情報はチャット出力にも書かない。
- 数値・銘柄名は原文どおり正確に表記する（勝手に略さない）。
- 台帳（records.json）を直接編集しない。操作は必ず `dividend.py` 経由。
- 集計の提示には毎回「投資助言ではない」旨と、誤読の可能性がある場合は要再確認点を添える。
