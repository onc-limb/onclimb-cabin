---
name: ultron-family-budget-manager
description: >-
  夫婦の共同支出(食費中心)のレシートを記録し、割り勘精算の元データとなる月次集計を作る。shared-expense-data/inbox/ のレシート画像を読み取って日付・店舗・税込合計・カテゴリを抽出し、購入月のアーカイブへリネーム移動して transactions.jsonl に追記、summary.md を再生成する。読み取りに失敗したファイルは推測で埋めず inbox に残して報告する。「レシート集計して」「inbox 整理して」「今月の食費まとめて」「割り勘用の集計出して」等で起動。本スキルは世帯の共同支出のみを扱い、個人の家計全体(収入・固定費・収支)は対象外。
model: sonnet
effort: medium
metadata:
  type: skill
  data_dir: <repo>/shared-expense-data
  origin: budget-bot (skills/process-receipts) を 2026-07-03 に移植
---

# family-budget-manager — 家族のお金管理スキル

> **家族のお金（夫婦の共同支出、ほぼ食費）のレシートを月別に記録・集計するスキル。**
> 財布が別の夫婦間で「払った分を割り勘する」ための精算元データを作るのが目的。
> 処理は必ず下の手順を**上から順に**実行する（スキップ・順序入れ替え禁止）。

## 棲み分け（重要）

- **本スキル**: **家族のお金**（夫婦の共同支出、食費中心）の記録と月次合計。割り勘精算の元データ。**家族側**。
- 本人**個人**の資産管理（収入 − 固定費 − 変動費の収支）は本スキルの対象外
  （旧 `ultron-personal-budget-manager` は未使用のため廃止。必要になったら分割し直す）。
- 同じレシートを両方に入れない（共同支出のレシートは本スキル側のみ）。
- 事業経費は `ultron-tax-prep-organizer` の領分。

## 場所（コードとデータは分離されている）

- ツール本体: このスキルディレクトリ `.claude/skills/ultron-family-budget-manager/`（`scripts/` `references/`）
- **データ**: リポジトリ直下の `shared-expense-data/`（**git 管理外**。`.gitignore` 済み）。
  環境変数 `SHARED_EXPENSE_DATA` で場所を上書き可。
- **Google Drive 連携（任意）**: `inbox/` と `archive/` を Drive 上の実体へのシンボリックリンクにし、
  複数マシン・スマホと同期できる。セットアップと運用ルールは
  [`references/google-drive-sync-setup.md`](references/google-drive-sync-setup.md)。
  連携時は**書き込み処理（本スキルの実行）は 1 台（Mac Mini）だけ**という単一ライター原則を守る。
  他端末は inbox への投入と閲覧のみ。

```
shared-expense-data/
├── inbox/                    # 未処理レシート画像の投入先
│   └── _duplicates/          # 重複と判明したファイルの隔離先（対象外）
├── archive/                  # 月次アーカイブ
│   └── YYYY-MM/
│       ├── receipts/         # 処理済みレシート画像
│       ├── transactions.jsonl  # 抽出データの正本 (1 行 1 レコード)
│       └── summary.md        # jsonl から生成する人間向けビュー
└── logs/                     # 実行ログ (YYYY-MM.log)
```

## このスキルが守る不変条件

これらが崩れると精算データが壊れるので、常に成立させること。

- `inbox/` には未処理ファイルしか置かない（処理済みは archive へ移す）。
- 各レシートは**購入月**の `archive/YYYY-MM/` に入る（処理日基準ではない）。
- `transactions.jsonl` が唯一の正本。`summary.md` はそこから生成する派生ビュー。
- `summary.md` の合計は、jsonl を再計算した値と常に一致する。
- 画像の OCR は新規取り込み時の 1 回だけ。集計のたびに画像を読み直さない。

## 手順

```bash
SKILL=/Users/satoshi-onga/Documents/onclimb-industries/.claude/skills/ultron-family-budget-manager
DATA=${SHARED_EXPENSE_DATA:-<repo>/shared-expense-data}
```

### Step 1. inbox を列挙

```bash
ls -1 "$DATA/inbox/" | grep -viE '^\.gitkeep$|^\.DS_Store$|^_duplicates$'
```

対象が 0 件なら「処理対象なし」と報告して終了。1 件以上なら次へ。
`_duplicates/` などのサブフォルダは対象外。

### Step 2. 1 ファイルずつ抽出（Read で画像を読む）

各画像を Read ツールで開き、Vision で以下を読み取る。

| 項目 | 形式 | 取得ルール |
|------|------|-----------|
| `date` | `YYYY-MM-DD` | レシート上の購入日。年が省略されていたら実行年で補完 |
| `store` | 文字列 | 店舗名。「株式会社」「(株)」等の法人格は省く |
| `total` | 整数(円) | 「合計」「お買上げ計」「ご請求額」等の税込最終金額 |
| `category` | 文字列 | 下表から推測 |

カテゴリ推測ルール:

| カテゴリ | 店舗例 |
|----------|--------|
| 食費 | スーパー（ライフ/イオン/サミット/まいばすけっと/マルエツ等）、食料品中心のコンビニ |
| 日用品 | ドラッグストア、ホームセンター、100均、家電 |
| 交通費 | JR/私鉄/バス/タクシー/ガソリンスタンド |
| 外食 | 飲食店、カフェ、居酒屋、KFC等のファストフード、デリバリー |
| その他 | 上記に該当しないもの |

判別が曖昧なら `その他` にし、ログに `ambiguous category` として残す。

**抽出できないファイルはスキップして inbox に残す。** 次のいずれかなら失敗扱い:
`date` が読めない / `total` が読めない・0円以下 / 画像破損・Read エラー。
失敗時は `logs/<実行月>.log` に記録する（フォーマットは Step 5）。

判読が難しいファイルを無理に推測して間違った金額を登録するより、正直に
スキップしてユーザーに確認してもらう方がよい。ユーザーが後から正しい値を
教えてくれたら、その 1 件だけ手動で登録する（Step 3〜5 を 1 件分実行）。

### Step 3. リネームして購入月へ移動

新ファイル名: `<date>_<store>_<total>.<拡張子>`
（例: `2026-06-08_サミット_1392.jpg`）

- `store` から `/ \ : * ` と空白を除去（日本語はそのまま可）。
- 同名衝突時は末尾に `_2`, `_3`… を付ける。
- 元ファイル名（`IMG_1234.jpg` 等）は jsonl の `source` に残すので保持しておく。

```bash
mkdir -p "$DATA/archive/<YYYY-MM>/receipts"
mv "$DATA/inbox/<元ファイル>" "$DATA/archive/<YYYY-MM>/receipts/<新ファイル名>"
```

### Step 4. transactions.jsonl に追記（正本の更新）

`archive/<YYYY-MM>/transactions.jsonl` に **1 行**追記（無ければ新規作成）。
1 レコード 1 行の JSON Lines。複数行 JSON は禁止。

```jsonl
{"date":"2026-06-08","store":"サミット","category":"食費","total":1392,"file":"receipts/2026-06-08_サミット_1392.jpg","source":"IMG_7150.JPG","added_at":"2026-07-02T20:59:27+09:00"}
```

| フィールド | 型 | 説明 |
|-----------|-----|------|
| `date` | string `YYYY-MM-DD` | 購入日 |
| `store` | string | 店舗名 |
| `category` | string | 食費/日用品/交通費/外食/その他 |
| `total` | integer | 税込金額(円) |
| `file` | string | archive後の相対パス `receipts/...` |
| `source` | string | inbox 投入時の元ファイル名 |
| `added_at` | string ISO8601 | 追記日時(JST, タイムゾーン込み) |

追記ルール:
- 末尾に append するだけ（順序は問わない。並べ替えは summary 生成時に行う）。
- 同じ `source` のレコードが既にあれば追記せず warning ログ（重複取り込み防止）。
- 既存行は勝手に書き換えない（追記のみ。修正はユーザー指示時だけ）。

### Step 5. summary.md を再生成 + ログ記録

追記した各月について、同梱スクリプトで summary.md を再生成する。
**jsonl を全件フル再計算**するので、手動修正後の再生成にも使える。

```bash
python3 "$SKILL/scripts/regen_summary.py" "$DATA/archive/<YYYY-MM>"
```

スクリプトは jsonl だけを読み、画像は再 OCR しない。「月次確定」セクションは
既存 summary.md から引き継いで消さない。壊れた jsonl 行があれば停止して知らせる。

処理結果を `logs/<実行月>.log` に追記:

```
[YYYY-MM-DD HH:MM:SS] OK <新ファイル名> archive=<YYYY-MM> total=<金額> category=<カテゴリ>
[YYYY-MM-DD HH:MM:SS] FAIL <元ファイル> reason=<日付不明 / 金額不明 / 読み取り失敗>
```

### Step 6. 検算して報告

正本と派生の一致を確認してから報告する。jsonl を独立に集計した値と、
summary.md の合計行が一致すること・全 `file` が実在することを確かめる。

```bash
cd "$DATA" && python3 - <<'PY'
import json, os, glob
for jp in glob.glob("archive/*/transactions.jsonl"):
    d = os.path.dirname(jp)
    recs = [json.loads(l) for l in open(jp, encoding="utf-8") if l.strip()]
    tot = sum(r["total"] for r in recs)
    miss = [r["file"] for r in recs if not os.path.exists(os.path.join(d, r["file"]))]
    print(f"{os.path.basename(d)}: {len(recs)}件 合計¥{tot:,} 欠損:{miss or 'なし'}")
PY
```

ユーザーへの報告フォーマット:

```markdown
## 処理結果
- 成功: N 件 / 失敗: M 件（inbox に残留）

### 月別の追加分
- YYYY-MM: K 件追加 → 合計 ¥XX,XXX（前回 ¥YY,YYY）

### 失敗ファイル（要確認）
- <ファイル名>: <理由>
```

## エラー時の振る舞い

| 状況 | 対応 |
|------|------|
| inbox が空 | 「処理対象なし」と報告して終了 |
| 画像 1 枚の読み取り失敗 | スキップして次へ。FAIL をログ |
| mv 失敗（権限等） | その場で停止しユーザーに原因報告 |
| jsonl にパース不能な行 | スキップせず停止して報告（壊れたデータの上書き防止） |
| 同じ source のレコードが既存 | 追記スキップ + warning ログ |

**禁止事項**: 失敗ファイルを勝手に削除しない / inbox 以外の既存ファイルを書き換えない
（archive の既存レシートは読むだけ）/ summary.md の「月次確定」セクションを再生成で消さない。

## 品質・安全性（persona: ultron）

- **世帯の金銭情報は git 管理外**: `shared-expense-data/` は `.gitignore` 済み。店名・金額・購入品目を
  含むデータをスキルディレクトリ（git 管理内）やコミットに持ち込まない。
- **数値の正確性・再現性**: 集計はすべて `regen_summary.py` のフル再計算。Claude は合計を暗算せず、
  スクリプト出力と検算結果を転記する。
- **推測より正直なスキップ**: 判読できない項目は推測で埋めず、失敗として報告する。
- 出力は支出の機械的な記録・集計であって、家計指導・支出の評価はしない。
- 金額表記は `¥` + 3 桁カンマ区切り（例 `¥1,234`）。集計スクリプトが自動で整形する。
