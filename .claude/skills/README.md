# スキル命名規約

このディレクトリ配下のスキルは、**分類プレフィックス + スキル名** の形式で命名する。
ここがスキルの正本であり、スキルの追加・変更はこのディレクトリだけで行う。

```
<prefix>-<skill-name>/
```

- プレフィックスはスキルの分類を表す（下表）。
- ディレクトリ名と `SKILL.md` の `name:` フロントマターは必ず一致させる。
- スキルを新規追加・分類変更するときは、まずこの表に従ってプレフィックスを決める。
- 各分類の共通ルールは [`personas/<prefix>.md`](../../personas/) にある。新規スキルは対応する
  persona を参照して設計する（[ルート `CLAUDE.md`](../../CLAUDE.md) 参照）。
- **プレフィックスは業務側リポジトリ onclimb-industries と重複させない**（2026-08-14 ルール化。
  jarvis / friday / arc-reactor / edith / karen は業務側専用、下表が当リポジトリ専用）。
  命名の書式（`<prefix>-<skill-name>`）と運用ルールは両リポジトリ共通。

## プレフィックス一覧（このリポジトリ専用）

| プレフィックス | 分類 | 説明 |
|----------------|------|------|
| `homer` | 一次資料・ノート系 | 自分自身が見る学習・思考の記録（読書メモ・モデリングノート） |
| `ultron` | 事務・金融・資産系 | 事務作業・金融・資産運用（単価相場などの調査を含む） |
| `griot` | 練習・コーチング系 | 設計トレーニングなど、自分の力を鍛える個人練習・コーチング。聞き手・読み手は自分自身 |
| `vision` | プライベート・人間関係系 | 仕事の成果物ではない私生活領域（人間関係の記録・家族・個人の暮らし）。読み手は自分だけ |
| `pepper` | 対外発信・公開ドキュメント系 | 技術記事・登壇資料・公開プロフィールなど、不特定多数に公開する個人の発信物。業務側の一次情報（worklog / knowledge-base / capture）を読み取り一方向で材料にする |

## 現在のスキル

| スキル | 分類 |
|--------|------|
| `homer-reading-notes` | 一次資料・ノート系 |
| `homer-modeling-notes` | 一次資料・ノート系 |
| `ultron-invoice-builder` | 事務・金融・資産系 |
| `ultron-contract-review-assistant` | 事務・金融・資産系 |
| `ultron-tax-prep-organizer` | 事務・金融・資産系 |
| `ultron-dividend-recorder` | 事務・金融・資産系 |
| `ultron-family-budget-manager` | 事務・金融・資産系 |
| `ultron-high-dividend-stock-screener` | 事務・金融・資産系 |
| `ultron-portfolio-analyzer` | 事務・金融・資産系 |
| `ultron-freelance-rate-research` | 事務・金融・資産系 |
| `ultron-company-research` | 事務・金融・資産系 |
| `griot-modeling-drill` | 練習・コーチング系 |
| `griot-project-doc-reviewer` | 練習・コーチング系 |
| `vision-people-memory` | プライベート・人間関係系 |
| `pepper-tech-article-drafter` | 対外発信・公開ドキュメント系 |
| `pepper-skillset-writer` | 対外発信・公開ドキュメント系 |
| `pepper-zenn-publisher` | 対外発信・公開ドキュメント系 |

## 補足

- **モデル割り当て**: スキル・作業ごとの適正モデルは
  [`personas/model-selection.md`](../../personas/model-selection.md) に従う。
- **文章表現の共通ルール**: 文書を生成するスキルは
  [`personas/writing-style.md`](../../personas/writing-style.md) に従う。
- **データ出力ディレクトリ名は改名対象外**。各スキルがリポジトリ直下に生成するデータ置き場や
  スクリプト内部のモジュール名・環境変数は、既存データとの整合を保つため旧名のまま運用する。
