---
name: ultron-portfolio-analyzer
description: >-
  SBI 証券のポートフォリオ CSV(保有一覧)を取り込み、セクター傾向・払った額に対する株価損益・配当込みの総損益・集中度・勝ち負け・口座区分別配分を分析する。決定論スクリプトが Shift_JIS のマルチセクション CSV をパースして正規化保存し(損益・利回りを暗算しない)、銘柄コード→東証33業種の台帳でセクターを付与、配当・分配金履歴 CSV から配当込み総損益を算出する。最新 CSV を渡すたび時系列 snapshot が積まれ、前回との差分も出る。「この証券口座のCSVを分析して」「保有株のセクターの傾向を見て」「配当込みの総損益を出して」等の明示依頼時のみ起動(自動起動しない)。投資助言ではない。これから買う銘柄の選定は ultron-high-dividend-stock-screener、配当書類の記録は ultron-dividend-recorder の領分。
model: sonnet
effort: medium
metadata:
  type: skill
  data_dir: <repo>/portfolio-data
---

# portfolio-analyzer — 証券ポートフォリオの取り込み・分析スキル

SBI 証券のポートフォリオ CSV（保有一覧）と配当・分配金履歴 CSV を取り込み、
**セクター傾向 / 株価損益（払ったお金に対する）/ 配当込み総損益 / 集中度 / 勝ち負け /
口座区分別配分 / 前回比** を分析する。定期的に最新 CSV を渡すと時系列で積まれる。

> **スタンス（毎回明示）**: 本スキルの出力は**保有情報の機械的な整理・集計であって、投資助言ではない**。
> セクター分類・配当は登録済みの台帳に依存し、未登録・未取り込み分は集計に含まれない。

## 棲み分け

- **`ultron-high-dividend-stock-screener`**: これから買う高配当株の選定（未来）。
- **`ultron-dividend-recorder`**: 受け取った配当書類の画像から実績を記録（過去の 1 件ずつ）。
- **本スキル**: **今保有しているポートフォリオ全体**の損益・構成の分析（現在のスナップショット）。
- 配当込み総損益の配当は SBI の配当履歴 CSV（税引後）を本スキルの `dividends.json` に別途累積する。
  dividend-recorder の `dividend-data/records.json`（税引前・画像由来）とは**別台帳**で、混ぜない。

## 場所（コードとデータは分離されている）

- ツール本体・参照資料: このスキルディレクトリ `.claude/skills/ultron-portfolio-analyzer/`
- **データ**: リポジトリ直下の `portfolio-data/`（**git 管理外**。`.gitignore` 済み。
  証券口座の保有銘柄・取得単価・損益を含むため絶対に追跡・push しない）。
  ```
  portfolio-data/
  ├── inbox/                        # ユーザーが置く生 CSV（保有一覧 / 配当履歴）
  ├── snapshots/holdings-<date>.json  # 正規化した保有スナップショット（時系列で積む）
  ├── dividends.json                # 受取配当・分配金の累積台帳（重複排除）
  ├── sector-map.json               # 銘柄コード→東証33業種のマッピング台帳（積み増し）
  └── reports/analysis-<date>.md    # 生成した分析レポート
  ```
- データディレクトリは `--data-dir` または環境変数 `PORTFOLIO_DATA` で上書き可。
- **CSV は必ず `portfolio-data/inbox/` に退避してから処理する**。リポジトリ直下や
  他の場所に置かれた保有・配当 CSV を見つけたら、まず inbox へ移動する（git に乗せない）。

## 役割分担（重要）

- **Claude（あなた）**: CSV を inbox へ退避、`sector missing` で未登録銘柄を確認して
  公開情報（Yahoo!ファイナンス／かぶたん／会社四季報等）から東証33業種を調べて `sector set` で登録、
  分析結果の要点の説明、免責の付与。
- **スクリプト（決定論）**: `scripts/portfolio.py` が CSV パース・正規化・検算・重複排除・
  損益/利回り/構成比/集中度の計算・レポート生成をすべて行う。

→ **Claude は損益・利回り・合計を暗算しない**。数値は必ず `portfolio.py` の出力を転記する。
→ セクター分類は**公開情報で裏を取ってから**登録する（推測で入れた場合は要確認として伝える）。

## CSV の形式（`references/csv-format.md` に詳細）

- **保有一覧 CSV**: SBI「ポートフォリオ一覧」のエクスポート。Shift_JIS。1 ファイルに
  「株式（現物/NISA…）」「投資信託（金額/特定…）」など**複数セクション**が入り、各区分に合計行が付く。
  個別株は `"コード 銘柄名"` の順、明細列は `名称,買付日,数量,取得単価,現在値,前日比,前日比%,損益,損益%,評価額`。
- **配当・分配金履歴 CSV**: SBI「取引履歴 > 配当金・分配金」のエクスポート。上部にサマリ、
  下部に明細 `受渡日,口座,商品,銘柄名,数量,受取額(税引後・円)`。銘柄名は `"銘柄名 コード"` の順（保有と逆）。
  **金額は税引後（手取り）**。
- パーサはエンコーディング（Shift_JIS/UTF-8）と列の並びを自動判定する。SBI の出力仕様が
  変わって取り込めない場合は `references/csv-format.md` を更新し、パーサを調整する。

## フロー A: 初回の取り込み・分析（メイン）

1. **CSV の退避**: ユーザーが渡した保有一覧 CSV（と、あれば配当履歴 CSV）を
   `portfolio-data/inbox/` へ移動する。ファイル名は `holdings-<date>.csv` / `dividends-<date>.csv` を推奨。
2. **保有一覧の取り込み**:
   ```bash
   python3 .claude/skills/ultron-portfolio-analyzer/scripts/portfolio.py \
     import-holdings --file portfolio-data/inbox/holdings-<date>.csv --date <YYYY-MM-DD>
   ```
   検算表（明細合計 vs CSV 記載の合計行）を確認する。`!!` があればパースを疑う。
3. **セクターの登録**: `sector missing --date <date>` で未登録銘柄を列挙し、各銘柄の東証33業種を
   公開情報で確認して `sector set --code <code> --name "<名>" --sector "<業種>"` で登録する
   （`references/sector-taxonomy.md` の33業種名を使う。確信が持てない中小型株は Web で裏取り）。
4. **配当履歴の取り込み（配当込み総損益を出すなら）**:
   ```bash
   python3 .claude/skills/ultron-portfolio-analyzer/scripts/portfolio.py \
     import-dividends --file portfolio-data/inbox/dividends-<date>.csv --dry-run   # 確認
   python3 .claude/skills/ultron-portfolio-analyzer/scripts/portfolio.py \
     import-dividends --file portfolio-data/inbox/dividends-<date>.csv            # 本取り込み
   ```
   重複は `(code, payDate, amount)` で自動スキップされるので、やり直しても二重計上にならない。
5. **分析**:
   ```bash
   python3 .claude/skills/ultron-portfolio-analyzer/scripts/portfolio.py analyze --date <date> --save
   ```
   `reports/analysis-<date>.md` に保存しつつ標準出力にも出る。要点（総損益・配当込み・セクター偏り・
   集中度・勝ち負け）を日本語で要約し、免責を添えてユーザーに報告する。

## フロー B: 定期更新（2 回目以降）

最新 CSV を受け取るたびに **A-1 → A-2（新しい日付で）→ A-4 → A-5** を回す。
- 新規銘柄があれば `sector missing` に出るので都度 `sector set` で積み増す（既登録は再利用）。
- `analyze` は自動で**直前の snapshot との差分**（評価額推移・含み損益推移・新規/売却銘柄）を出す。
- スナップショットは上書きしない（同日は `--force` で明示上書き）。時系列は消さずに積む。

## フロー C: セクター台帳のメンテナンス

- `sector list` で登録一覧、`sector set` で追加・修正（上書き）。
- 分類は東証33業種で統一する（`sector-taxonomy.md`）。33業種一覧に無い値を渡すと警告が出る。
- 企業の統合・業種変更があれば `sector set` で更新する。

## 分析で出るもの（`references/analysis-guide.md` に定義）

1. **概況（払ったお金に対する損益）**: 取得額（= 評価額 − 損益）・評価額・含み損益・損益率を
   個別株／投資信託／合計で。
2. **配当を含めた総損益**: 受取配当累計（税引後）・取得額ベース配当利回り・配当込み総損益・
   配当込みトータルリターン率。
3. **セクター傾向（個別株）**: 業種別の評価額・構成比・取得額・含み損益・損益率・銘柄数。
4. **口座区分別の配分**: 現物/NISA成長枠/つみたて枠/旧つみたて/特定 ごとの損益。
5. **集中度**: 保有銘柄数・最大銘柄比率・上位5銘柄比率・HHI。
6. **勝ち・負け銘柄**: 損益率のトップ5／ワースト5。
7. **前回比**: 直前 snapshot があれば評価額・含み損益の変化と新規/売却銘柄。

## 品質・安全性

- `portfolio-data/` は**個人資産情報**。CSV・snapshot・レポートを git に乗せない
  （`.gitignore` 済み。チャット出力でも口座番号等の機微情報は書かない）。
- 数値・損益・利回りは `portfolio.py` の出力を転記し、Claude が暗算しない。
- 取得額は「評価額 − 損益」を全区分共通の真値とし、個別株は取得単価×数量で検算する。
- セクター分類・配当は台帳ベース。未登録・未取り込みがあればレポートに明示される。
- 最終アウトプットには毎回、投資助言ではない旨と要再確認点（未分類銘柄・CSV との照合推奨）を添える。
