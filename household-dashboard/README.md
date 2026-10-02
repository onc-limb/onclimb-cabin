# household-dashboard — 夫婦の家計精算ダッシュボード

夫婦（智史・香琳）の共通支出を精算する CLI と、閲覧用の Grafana。
2026-10 に onclimb-industries の personal-dashboard（Elixir 版の家計タブと Rust 移植版）から移した。

## 全体像

```
① レシート（食費・外食・日用品・立て替え）           ② 手入力（光熱費・家賃相殺・振込・その他の立て替え）
   shared-expense-data/archive/YYYY-MM/                 household-data/ledger/records.json
   transactions.jsonl                                   （household add / pass で書く）
   （ultron-family-budget-manager が書く）
                 └──────────────┬──────────────┘
                                ▼
                     household（Rust CLI）が精算計算
                                ▼
                 household-data/household.db（SQLite 派生ビュー）
                                ▼
                     Grafana（http://localhost:3001）
```

- 正本は ① と ② の 2 つ。SQLite はそこから毎回作り直す派生ビューで、直接編集しない。
- `household-data/` は git 管理外。`ledger/` は Google Drive
  （マイドライブ/onclimb-industries/家計台帳）へのシンボリックリンクで、`household.db` はローカルに置く。

## 精算ルール

残高は常に「香琳が智史に払うべき額」（正なら智史の立て替え超過）。

| 種別 | 意味 | 智史が支払/送金 | 香琳が支払/送金 |
|---|---|---|---|
| 共同 | 折半（50/50） | +金額/2 | -金額/2 |
| 個別 | 立て替え（100% 相手負担） | +金額 | -金額 |
| 調整 | 残高の手動調整（個別と同じ） | +金額 | -金額 |
| 振込 | 現金の移動 | +金額 | -金額 |

- **家賃相殺**: 智史は毎月 63,700 円を家賃として香琳に振り込む。立て替え残高が 1 か月分以上に
  溜まったら、その月の振込を止めて相殺する（`household pass`。台帳には 振込・香琳→智史 で記録）。
- **レシートの既定値**: `payer`・`kind` の無いレシートは「智史・共同」として計算する。
  例外は `household receipt` で直す。
- **レシートの計上開始月**: `ledger/config.json` の `receiptsFrom`（既定 `2026-08`）より前の
  レシートは精算に入れない。2026-03〜05 は台帳に月次食費として手入力済みのため。
  2026-06・07 分は、2026-07-26 の「諸々立て替え精算」に含まれるか未確認のため保留している。

## 使い方

```bash
cd household-dashboard
cargo run -q -- balance                       # 現在残高・家賃相殺の提案・今月の内訳
cargo run -q -- list --month 2026-08          # 明細（手入力 + レシート）
cargo run -q -- add --kind 共同 --payer 智史 --category 電気 --amount 6477 --date 2026-08-31
cargo run -q -- add --kind 個別 --payer 香琳 --category その他 --item 薬 --amount 1200
cargo run -q -- add --kind 振込 --payer 香琳 --to 智史 --amount 30000
cargo run -q -- pass --month 2026-10 --dry-run  # 家賃相殺（--amount で一部相殺額を指定）
cargo run -q -- receipt --ref 2026-08/IMG_1234.JPG --payer 香琳 --kind 個別
cargo run -q -- delete --id 103
cargo run -q -- export                        # SQLite を作り直す（書き込み系は自動で実行）
```

カテゴリ: 電気 / 水道 / ガス / 食費 / 外食 / ビール / 日用品 / 交通費 / 旅行 / 家具 / その他 / 振込 / 初期残高

月次ルーチンの目安: 月末日付で 電気・ガス（毎月）・水道（隔月）・ビール（定期購入がある月）を
共同で入れ、`balance` で残高を見て `pass` を判断する。

環境変数で置き場所を上書きできる: `HOUSEHOLD_DATA_DIR` / `HOUSEHOLD_RECORDS_PATH` /
`HOUSEHOLD_SQLITE_PATH` / `SHARED_EXPENSE_DATA`。

## Grafana

```bash
cd household-dashboard/grafana
docker compose up -d     # → http://localhost:3001
```

パネル: 現在残高 / 家賃相殺の提案 / 今月の共同支出 / 残高推移 / 月別 共同支出（カテゴリ積み上げ） /
光熱費 / 月次精算 / 店舗別（レシート） / 明細。表示は SQLite を読むだけなので、
CLI で書き込めば 5 分以内（またはリロード）で反映される。

## 品質ゲート

```bash
cargo test
cargo clippy --all-targets -- -D warnings
cargo fmt --check
```

テストはすべて一時ディレクトリで動き、`household-data/` には書き込まない。
SQLite の書き出しには `sqlite3` CLI を使う。

## 移行記録（2026-10-02）

- records.json（102 件）を industries の `data/household/` から Google Drive へ複製した。
- 移行時点の残高は 香琳 → 智史 108,954.5 円。旧 Elixir 版 `Dashboard.Household.delta/1` の式で
  独立に計算した値と一致することを確認した。
