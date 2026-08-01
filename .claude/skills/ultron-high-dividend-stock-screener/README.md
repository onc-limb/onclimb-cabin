# high-dividend-stock-screener

日本の高配当株を screening して「おすすめ候補リスト」を作るスキル。
公開情報（Yahoo!ファイナンス / IR BANK / EDINET 等）から **配当利回り 4% 以上**の日本株を拾い、
**健全性**（5年減配なし・配当性向 50% 未満・10年営業黒字・売上/EPS 右肩上がり(5年)・
自己資本比率 40% 以上・営業CF 黒字(10年)）と**株価上昇余地**（増配率・EPS成長率 年5%以上・ROE 8%以上）の
**コア11条件**で篩い、**REIT / 投資法人 / インフラファンド**を除外する
（投資目的: 配当 4.5% + 数年トータルで株価 +20% の両取り）。調べた会社は**法人番号で台帳に記録**して
回を分けて積み増し（続きから再開可）、最後に **Claude のレビュー**を添える。

> 投資助言ではなく**情報整理**。最終判断は自己責任。詳細は `SKILL.md` の免責を参照。

## 構成

```
high-dividend-stock-screener/
├── SKILL.md                     # 起動条件・標準フロー・免責（本体）
├── README.md
├── .gitignore
├── config/
│   └── screener.yaml            # しきい値・除外パターン・batch_size
├── bin/
│   ├── hdss_lib.py              # 共通ライブラリ（パス解決/YAML/台帳/除外/判定）依存ゼロ
│   ├── resolve_corp.py          # 証券コード→法人番号（EDINET コードリスト突合）
│   ├── registry.py              # 調査済み台帳 status / filter / add / update
│   └── judge.py                 # コア11条件の決定論的判定
├── references/
│   ├── screening_rules.md       # 判定軸の定義・除外ルール（進化対象）
│   ├── site_structure.md        # 取得元サイトの構造メモ（進化対象）
│   └── review_checklist.md      # Claude レビューの観点（進化対象）
├── templates/
│   └── list.md                  # おすすめリストの雛形
└── logs/                        # 自己進化用の実行ログ置き場（.gitkeep）
```

データ（台帳・母集団カーソル `registry/cursor.json`・リスト・EDINET キャッシュ）は**コードと分離**し、リポジトリ直下の
`stock-data/`（`STOCK_DATA` で上書き可）に置く。`stock-data/` は機密を含みうるため
リポジトリの `.gitignore` で追跡対象外。

## 役割分担

数値の捏造を防ぐため **取得・名寄せ・レビューは Claude、合否判定はスクリプト**に分離。
`judge.py` だけが合否を決め、Claude は現在値の取得と JSON 整形・講評に専念する。

## 主なコマンド

```bash
SKILL=.claude/skills/ultron-high-dividend-stock-screener
python3 "$SKILL/bin/registry.py" status                       # 台帳の累計
python3 "$SKILL/bin/registry.py" filter --stdin               # 未調査だけ抽出（重複調査防止）
python3 "$SKILL/bin/judge.py"     --file companies.json       # コア11条件で合否
python3 "$SKILL/bin/resolve_corp.py" 7203 8058                # 法人番号解決（初回は EDINET 自動DL）
python3 "$SKILL/bin/registry.py" add --stdin                  # 台帳へ追記（合否に関わらず）
python3 "$SKILL/bin/registry.py" update --stdin               # 既存行を置換（再検証・mode=new の反映）
```

セルフテスト: `python3 bin/hdss_lib.py`
