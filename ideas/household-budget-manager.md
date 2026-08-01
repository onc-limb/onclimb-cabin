---
name: household-budget-manager
status: shipped
created: 2026-07-02
updated: 2026-07-02
tags: [finance, household, budget, receipt, gmail]
related: [tax-prep-organizer.md, invoice-builder.md]
---

# household-budget-manager

> フリーランスエンジニア本人が、月末や買い物後に「レシート・領収書・領収書メール」を投げ込むだけで、
> 個人の家計（収入 − 固定費 − 変動費）の月次収支を正確に把握するためのスキル。

---

## 場面 (When)

- 買い物のレシートを撮った画像 / もらった PDF を `budget-data/inbox/` に置いて「これ読み込んで」と言ったとき
- 月末・月初に「今月の家計をまとめて」「収支を出して」と言ったとき
- EC サイトの購入確認メールや領収書メールが溜まっていて「Gmail から拾って」と言ったとき
- 家賃・サブスク等の固定費が変わったので config を更新したいとき
- 「先月より食費が増えてる気がする」など、カテゴリ別の推移を確認したいとき

### 想定トリガー文 (実際のユーザー発話例)

1. 「今月の家計をまとめて」
2. 「このレシート読み込んで」（inbox に画像 / PDF を置いた直後）
3. 「Gmail から領収書メールを拾って」
4. 「今月の収支どうなってる？」
5. 「固定費を更新して」（家賃改定・サブスク解約など）
6. 「先月と比べて食費どうなってる？」
7. 「inbox のレシート全部処理して」

---

## 解決する課題 (Problem)

- 現状: レシートは財布に溜まり、EC の領収書はメールに埋もれ、固定費は頭の中。月の支出総額を
  正確に言えず、家計簿アプリは入力が続かない（手入力の摩擦・カテゴリ選択の面倒さ）。
- 痛みの種類: 時間（手入力）、認知負荷（カテゴリ判断・メール探し）、見落とし（メール内領収書の取り漏れ）、
  誤り（暗算による集計ミス・二重計上）。
- 影響範囲: 個人の家計。ただし収支が見えないことは資金繰り・生活防衛資金の判断に波及する。

---

## 解決策 (Solution)

1. **レシート取り込み**: `budget-data/inbox/` の画像 / PDF を Read ツールで読み取り、Claude が
   `{date, store, amount, category, payment_method}` の明細 JSON に起こす → ユーザー確認 →
   月次エントリ `entries/YYYY-MM.json` へ追記 → 処理済みファイルは `inbox/processed/` へ移動。
2. **Gmail 取り込み**（MCP 接続時のみ）: 領収書系メールを検索し、
   本文に金額があるもの → 本文から明細抽出して取り込み /
   PDF 添付のもの → 「家計/DL待ち」ラベルを付けて手動 DL を依頼 /
   外部サイト誘導のもの → 「家計/要確認」ラベルを付けて一覧報告。
3. **月次収支集計**: 固定費 config + 変動費エントリ + 収入 config を決定論スクリプト
   `scripts/aggregate.py` が集計し、月次レポート（JSON + Markdown）を生成。Claude は合計を暗算しない。
4. **完了判定**: 「エントリ N 件 / 重複疑い M 件 / Gmail 要確認 K 件」を提示し、M・K が
   残っている場合は収支が未確定である旨を明示する。

### 入力 (Inputs)

| 名前 | 型 | 必須 | 出どころ | 例 |
|---|---|---|---|---|
| receipt_files | image/PDF ファイル群 | no | `budget-data/inbox/` | `IMG_0123.jpg`, `receipt.pdf` |
| gmail_threads | Gmail スレッド（MCP 経由） | no | `search_threads` / `get_thread` | 「ご購入ありがとうございます」メール |
| fixed_costs | JSON | yes（収支計算時） | `budget-data/config/fixed-costs.json` | `{"name":"家賃","amount":90000,...}` |
| income | JSON | yes（収支計算時） | `budget-data/config/income.json` | `{"default":[{"name":"業務委託報酬","amount":800000}]}` |
| target_month | string (YYYY-MM) | yes | ユーザー発話（省略時は当月） | "2026-07" |
| user_confirmation | 対話 | yes | 読み取った明細の確認 | 「金額 OK / 店名修正」 |

### 出力 (Outputs)

| 名前 | 形式 | どこに置く | 例 |
|---|---|---|---|
| 月次エントリ | JSON | `budget-data/entries/YYYY-MM.json` | 明細（date/store/amount/category/payment_method/source/gmail_message_id） |
| 集計結果 | JSON | `aggregate.py` の `--out` 出力 | totals / variable_by_category / duplicates_suspected / warnings |
| 月次レポート | Markdown | `budget-data/reports/YYYY-MM.md` | 収入・固定費・変動費・収支、カテゴリ別内訳、前月比、要確認リスト |
| Gmail ラベル付け結果 | チャット報告 + Gmail ラベル | Gmail（`家計/DL待ち` `家計/要確認` `家計/取込済`） | 「DL待ち 2 件、要確認 1 件」の一覧 |

---

## 想定ユーザー (Who)

- 主: 日本のフリーランスエンジニア本人（個人の家計管理。単身または家計を自分で管理している人）
- 副: なし（他者共有は想定しない。レポートは自分用）

持っている知識: JSON を読める。git / CLI 操作に抵抗がない。
持っていない知識: 家計簿の複式簿記的な整理法（不要。カテゴリは references の一覧で足りる粒度にする）。

---

## 既存ツール・スキルとの差別化 (Differentiation)

| 代替 | 限界 | このスキルが上回る点 |
|---|---|---|
| 家計簿アプリ（マネーフォワード等） | 手入力 or 口座連携の設定が重い。レシート OCR は精度・カテゴリが固定 | Read ツールでレシートを読み、対話で確認・修正しながら取り込める。データは手元の JSON |
| `ultron-tax-prep-organizer` | **事業経費の確定申告整理**が目的。勘定科目・按分・青色申告前提で、私的支出は対象外 | 本スキルは**個人の家計（私的支出 + 生活収支）**が対象。家計カテゴリ・固定費/変動費の軸で整理する |
| `ultron-invoice-builder` | 請求書の作成（売上側の事務）であり支出管理はしない | 収入は config の値として参照するだけで、支出側の管理が本体 |
| Gmail を手で検索 | 領収書メールの探索・仕分けが毎月の手作業。取り漏れが出る | 検索 → 本文抽出 / ラベル仕分けまで自動化し、取れないもの（添付・外部サイト）は明示的に残タスク化する |

---

## 成功条件 (Success Criteria)

- [ ] inbox にレシートを 5 枚置いて「処理して」の一声で、全件が明細 JSON 化 → ユーザー確認 → `entries/YYYY-MM.json` 追記 → `inbox/processed/` 移動まで完了する
- [ ] 月次レポートの収支（収入 − 固定費 − 変動費）が `aggregate.py` の JSON 出力と 1 円も違わず一致する（Claude の暗算値が混入しない）
- [ ] 同一の日付・金額・店舗のエントリを 2 回取り込もうとしたとき、レポートの「重複疑い」に 100% 挙がる
- [ ] Gmail 取り込み後、「本文から取り込んだ件数 / DL待ちラベル件数 / 要確認ラベル件数」がチャット報告と Gmail 上のラベルで一致する
- [ ] Gmail MCP 未接続のセッションでも、レシート取り込みと月次収支集計がエラーなく完走する（縮退動作）

---

## 失敗モード予測 (Failure Modes)

| 失敗 | 起きる条件 | 回避策 |
|---|---|---|
| レシート読み取り誤り（金額の OCR ミス） | 印字がかすれた感熱紙・斜め撮影・合計と小計の取り違え | 読み取った明細は**必ずユーザー確認を挟んでから**エントリに追記する。合計金額と明細の整合が取れない場合はその旨を明示して確認する |
| 二重計上 | 同じレシートを 2 回取り込む / レシートと Gmail 本文の両方から同じ買い物を取り込む | `aggregate.py` が同一「日付+金額+店舗」の 2 件目以降を重複疑いとして警告。Gmail 取り込みは `gmail_message_id` をエントリに記録し、既取り込み ID はスキップ。処理済みレシートは `inbox/processed/` へ移動して再処理を防ぐ |
| Gmail 添付の領収書が取れない | Gmail MCP に添付ダウンロードツールが存在しない（制約） | 添付型は取り込まず「家計/DL待ち」ラベルを付け、ユーザーに手動 DL → `budget-data/inbox/` 配置を依頼する運用に倒す。外部サイト誘導型は「家計/要確認」ラベル + 一覧報告 |
| カテゴリ誤分類 | 店名から用途が判別できない（例: ドンキで食費か日用品か） | `references/categories.md` の判定基準に従い、確証がなければ「その他」+ メモにして確認時に提示。ユーザー確認ステップで修正できる |
| 固定費の当月適用漏れ / 過剰適用 | 月途中で解約したサブスク・家賃改定 | fixed-costs.json に `active_from` / `active_until`（YYYY-MM）を持たせ、スクリプトが対象月で機械的にフィルタする |
| 収支の暗算ミス | Claude がレポートの数値を自分で計算してしまう | 合算・収支計算はすべて `aggregate.py` に委譲し、Claude は出力の転記のみ（tax-prep-organizer / invoice-builder と同じ規律） |

---

## 必要なリソース (Resources)

- 外部 API / CLI: Gmail MCP（`search_threads` / `get_thread` / `list_labels` / `create_label` / `label_message` / `label_thread` / `unlabel_message` / `unlabel_thread` / `create_draft` / `list_drafts` のみ。**添付ダウンロード不可**）、`python3`（標準ライブラリのみ）
- 権限: Gmail の読み取り・ラベル操作（MCP 接続時のみ）。ローカルは `budget-data/` の読み書き
- 参照すべき公開知識: なし（家計カテゴリは references に自前定義）
- ローカル前提: onclimb-industries リポジトリ直下に `budget-data/`（git 管理外、`BUDGET_DATA` で上書き可）

---

## 実装メモ (Implementation Notes)

- ディレクトリ構成: `.claude/skills/ultron-personal-budget-manager/`（`SKILL.md` / `scripts/aggregate.py` / `templates/monthly-report.md` / `references/categories.md` / `references/gmail-integration.md`）
- データ: `budget-data/config/fixed-costs.json`・`income.json`、`entries/YYYY-MM.json`、`inbox/`（+ `processed/`）、`reports/YYYY-MM.md`
- 既存スキルとの関係: `ultron-tax-prep-organizer` と同じ「分類は Claude・合算はスクリプト」の役割分担を踏襲。
  事業経費として確定申告に回すべき支出を見つけたら tax-prep-organizer 側に案内する（本スキルには混ぜない）
- scripts に切り出す処理: カテゴリ別合計・固定費/変動費/収入の合算・収支・重複疑い検出・前月比（前月ファイルがあれば）
- references に残す知識: 家計カテゴリ定義（判定基準付き）、Gmail 検索クエリ・ラベル運用 **(ASSUMPTION: ラベル名は `家計/取込済` `家計/DL待ち` `家計/要確認` の 3 種で開始。運用しながら手動で調整する)**

---

## 自己進化を入れるか (Self-Evolution Scope)

- [ ] パイプラインあり
- [x] パイプラインなし — 理由: 金銭の集計は決定論スクリプトで固定すべき領域であり、SKILL.md や集計ロジックが
  使用ログで自動書き換えされると数値の再現性・信頼性が壊れる。カテゴリ定義や Gmail クエリの改善は
  本人確認済みの手動更新（references の追記）で十分に回る。

---

## 実装リンク

- スキルディレクトリ: `../.claude/skills/ultron-personal-budget-manager/`
  （2026-07-03 に `ultron-household-budget-manager` から改名。家族のお金管理
  `ultron-family-budget-manager` の追加に伴い、個人の資産（現金）管理という立ち位置を名前に反映）
- 関連 PR / commit: （初版作成時点では未コミット）
