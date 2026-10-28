---
name: ultron-household-ledger
description: >-
  夫婦の家計精算台帳(household-data/ledger/records.json、git 管理外)にレシートの無い支出を手入力し、精算残高を確認する。光熱費(電気・水道・ガス)・ビール定期便などの共同支出(50/50)、相手の分の立て替え(100%)、振込、家賃振込の相殺を household CLI 経由で記録し、Grafana の家計ダッシュボード(http://localhost:3001)用の SQLite を更新する。残高・家賃相殺の提案・月の内訳は CLI の出力を転記し暗算しない。「今月の電気代を入れて」「香琳の分を立て替えたから記録して」「精算残高いくら？」「今月の家賃パスできる？」「家計ダッシュボード起動して」等の明示依頼時のみ起動(自動起動しない)。レシート画像の取り込みは ultron-family-budget-manager の領分。
model: sonnet
effort: low
metadata:
  type: skill
  data_dir: <repo>/household-data
---

# household-ledger — 家計精算台帳の手入力・確認スキル

夫婦（智史・香琳）の共通支出の精算台帳に、**レシートの無い支出**（光熱費・振込・家賃相殺・
その他の立て替え）を記録し、精算残高を確認する。計算・保存はすべて
`household-dashboard/` の Rust CLI（`household`）が行う。

> **スタンス**: 出力は支出の機械的な記録と精算額の計算であって、家計指導・支出の評価はしない。

## 棲み分け

- **本スキル**: 手入力台帳（入力②）への記録と、残高・精算の確認。
- **`ultron-family-budget-manager`**: レシート画像の取り込み（入力①。食費・外食・日用品・レシートのある立て替え）。
  **同じ支出をレシートと手入力の両方に入れない**。レシートがあるものは向こうで取り込む。
- 事業経費は `ultron-tax-prep-organizer` の領分。

## 場所

```bash
REPO=$(git rev-parse --show-toplevel)   # onclimb-cabin
cd "$REPO/household-dashboard"          # 以下のコマンドはここで実行する
```

- 台帳: `household-data/ledger/records.json`（Google Drive へのシンボリックリンク。git 管理外）。
  リンク切れのときは Google Drive アプリの起動を疑う。
- 精算ルール・コマンドの詳細は [`household-dashboard/README.md`](../../../household-dashboard/README.md)。
- **records.json を直接編集しない**。必ず CLI を通す（id 採番・検証・SQLite 更新が CLI 側にあるため）。

## 精算ルール（要点）

残高 = 香琳が智史に払うべき額（正なら智史の立て替え超過）。

| ユーザーの言い方 | 種別 `--kind` | 支払者 `--payer` |
|---|---|---|
| 二人の分の支出（光熱費・ビール・共用品） | `共同`（50/50） | 払った人 |
| 相手の分を立て替えた | `個別`（100%） | 立て替えた人 |
| 精算のために現金を渡した | `振込` + `--to <受取人>` | 渡した人 |
| 残高の手動調整 | `調整` | 調整で得をする側の逆（迷ったら確認する） |

## 手順

### A. 記録する

1. ユーザーの発言から 日付・内容・カテゴリ・種別・支払者・金額 を整理する。
   - カテゴリ: 電気 / 水道 / ガス / 食費 / 外食 / ビール / 日用品 / 交通費 / 旅行 / 家具 / その他 / 振込 / 初期残高
   - 光熱費は請求月の月末日付で入れる慣例（例: 9 月分の電気 → `2026-09-30`）。
   - **種別と支払者が言われていなければ推測せず聞く**（精算額が 2 倍ずれるため）。
     例外: 光熱費は過去の実態どおり「共同・智史」を提案して確認を取る。
2. 内容をユーザーに見せて確認を取ってから実行する。

```bash
cargo run -q -- add --date 2026-09-30 --kind 共同 --payer 智史 --category 電気 --amount 6477
cargo run -q -- add --kind 個別 --payer 香琳 --category その他 --item 智史の薬 --amount 1200
cargo run -q -- add --kind 振込 --payer 香琳 --to 智史 --amount 30000 --memo 9月分の精算
```

3. 出力の「追加しました [id] …」行と、`balance` の現在残高を転記して報告する。

### B. 残高・精算を確認する

```bash
cargo run -q -- balance --month 2026-09   # 現在残高・家賃相殺の提案・月の内訳
cargo run -q -- list --month 2026-09      # 明細（[id] は手入力、[R 月/source] はレシート）
```

### C. 家賃相殺（家賃パス）

智史は毎月 63,700 円を家賃として香琳に振り込む。残高が 1 か月分以上なら、その月の振込を止めて相殺する。

1. まず `--dry-run` で提案と記録後の残高を見せる。
2. ユーザーが了承したら `--dry-run` を外して記録する。一部相殺は `--amount` で金額を指定する。

```bash
cargo run -q -- pass --month 2026-10 --dry-run
cargo run -q -- pass --month 2026-10
```

同じ月を二重に記録しようとすると CLI が拒否する。

### D. 間違いを直す

`list` で id を確認し、`delete --id <id>` で消して入れ直す（削除前に対象行をユーザーに見せて確認する）。
レシート由来の行（`[R ...]`）の支払者・種別は `ultron-family-budget-manager` の Step 7 の手順で直す。

### E. ダッシュボードを開く

```bash
cd "$REPO/household-dashboard/grafana" && docker compose up -d   # → http://localhost:3001
```

Docker Desktop が起動していなければ先に起動する。書き込み系コマンドは SQLite を自動で作り直すので、
画面はリロードすれば反映される。

## 品質・安全性（persona: ultron）

- **世帯の金銭情報は git 管理外**: 台帳・SQLite・金額をスキルディレクトリやコミットに持ち込まない。
- **数値は CLI の出力を転記する**: 残高・合計・相殺額を暗算しない。
- **推測で記録しない**: 種別・支払者・金額が曖昧なら聞く。記録前に必ず確認を取る。
- 金額表記は 3 桁カンマ区切り（CLI の出力どおり）。
