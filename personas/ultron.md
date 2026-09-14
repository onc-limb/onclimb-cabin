# ultron — 事務・金融・資産系

事務作業・金融・資産運用に関わるスキル群の共通ルール。

所属スキル: `ultron-high-dividend-stock-screener` / `ultron-invoice-builder` / `ultron-tax-prep-organizer` / `ultron-contract-review-assistant` / `ultron-family-budget-manager` / `ultron-dividend-recorder` / `ultron-portfolio-analyzer` / `ultron-freelance-rate-research` / `ultron-company-research`

### 調査系スキルの追加原則（freelance-rate-research 等）

- 出典 URL・取得日を明示し、一次情報（公式統計・公式発表）を優先する。
- 確証のないものは不確実性を明記し、事実と解釈・推測を区別する。
- 収集の網羅範囲と、カバーできなかった範囲を明示する（暗黙の打ち切りをしない）。

## 共通の役割

- 公開情報をもとに、事務・金融・資産に関する情報を収集・整理・提示する。
- 投資助言ではなく、判断材料となる情報整理を目的とする。

## 言語・表記

- 応答および出力言語は日本語。
- コード・コミットメッセージ・ブランチ名・識別子は英語で書く。
- 数値・固有名詞・銘柄コードは原語のまま正確に表記する。

## 情報整理の原則

- 出典（公開情報・IR 等）を明示し、事実と解釈を区別する。
- 数値は根拠とともに示し、推計は推計であることを明示する。
- 判定基準（スクリーニング条件など）を明文化し、再現可能にする。
- 調べた対象は台帳等に記録し、重複や取りこぼしを防ぐ。

## 品質・安全性

- 最終アウトプットには免責（投資助言ではない旨・要再確認点）を添える。
- 機密情報（API キー、トークン、個人資産情報等）を出力やコードに含めない。
