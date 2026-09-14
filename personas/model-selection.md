# モデル割り当てガイド（スキル × モデル）

スキル・作業ごとに適したモデル（haiku / sonnet / opus / fable）を割り当てるための共通ルール。
単純作業に上位モデルを使うトークン非効率と、判断作業に下位モデルを使う品質劣化の両方を防ぐ。

## 仕組み（Claude Code で何ができるか）

| 手段 | 効果 | 制約 |
|---|---|---|
| SKILL.md frontmatter `model:` | スキルが起動した**そのターンだけ**モデルを一時切替 | 次のユーザープロンプトでセッションモデルに戻る。値: `haiku` / `sonnet` / `opus` / `fable` / フルモデル ID |
| サブエージェント（Agent tool の `model` 指定 / `~/.claude/agents/*.md` の `model:`） | 委任した作業だけ別モデルで実行 | **ステップ単位の切替はこれで実現する**（メイン会話はそのまま） |
| `/model` コマンド | セッションモデルをいつでも変更 | ユーザーのみ。Claude 自身はメインループのモデルを変えられない |
| `settings.json` の `env.ANTHROPIC_MODEL` / `CLAUDE_CODE_SUBAGENT_MODEL` | 既定モデルの固定 | セッション全体・サブエージェント全体に効く |
| 決定論スクリプト（各スキルの `scripts/*.py`） | **0 トークン** | 単純作業の第一選択はモデルではなくスクリプト |

出典: code.claude.com/docs の skills.md / sub-agents.md / agent-view.md（2026-07 確認）。

### ピン留めの注意（副作用）

- ピンは**起動ターンのみ**有効。Phase B のような複数ターンの対話フローでは、2 ターン目以降は
  セッションモデルに戻ることがある。**1 ターンに重い処理が収まるスキルほどピンが効く**。
- ピンは上下両方向に効く: fable セッションでも、ピンされたスキルのターンは sonnet で動く。
  品質劣化を観測したらピンを外す（[`ideas/skill-feedback.md`](../ideas/skill-feedback.md) に記録してレビューで判断）。

## ティアの使い分け方針

- **haiku 4.5**: 判断のない機械走査・列挙・単純取得を**サブエージェント委任するときだけ**使う。
  丸ごと haiku のスキルは作らない（対話確認・分類判断が必ず混ざり、事故のコストが節約を上回る）。
- **sonnet 5**: 定型・台帳・抽出系スキルの標準。該当スキルは frontmatter でピン留めする。
- **opus 4.8 / fable 5**: コード判断・設計・レビュー・発想・公開文章の品質が本体のスキル。
  ピンせず**セッションモデルを継承**する（どちらを使うかはユーザーが `/model` で選ぶ。
  目安: 通常は opus、大規模レビュー・設計・記事清書など最高品質が要る回は fable）。

## スキル別割り当て

「ピン sonnet」= SKILL.md frontmatter に `model: sonnet` を記載済み。「継承」= 記載なし（セッションモデル）。

### homer（一次資料・ノート系）

| スキル | 指定 | 理由・ステップ単位の委任 |
|---|---|---|
| homer-reading-notes | 継承 | キャプチャは軽いが、壁打ちの質はセッションモデルそのもの |
| homer-modeling-notes | 継承 | 思考の記録と観点フィードバックの質が本体 |

### pepper（対外発信・公開ドキュメント系）

| スキル | 指定 | 理由・委任 |
|---|---|---|
| pepper-tech-article-drafter | 継承（opus / fable 推奨） | 公開文章の品質が本体 |
| pepper-skillset-writer | 継承（opus / fable 推奨） | 外部公開するプロフィール文章の品質と誇張・機密混入の判定が本体 |
| pepper-zenn-publisher | ピン sonnet | frontmatter 整形・preview・git 操作の定型フロー。公開可否の最終判断はユーザー承認に委ねる設計 |

### ultron（事務・金融・資産系）

| スキル | 指定 | 理由・委任 |
|---|---|---|
| ultron-invoice-builder | ピン sonnet | 金額計算は全てスクリプト。明細整理のみ |
| ultron-tax-prep-organizer | ピン sonnet | 科目は候補付けのみ。判断が分かれる取引は設計上「要確認」行き |
| ultron-family-budget-manager | ピン sonnet | レシート読み取り（失敗は inbox 残しの設計） |
| ultron-dividend-recorder | ピン sonnet | 配当書類の読み取りと台帳追記。検算・集計はスクリプト |
| ultron-high-dividend-stock-screener | ピン sonnet | 公開情報の収集・機械的な篩い。銘柄ごとの個別取得は haiku 委任可 |
| ultron-contract-review-assistant | 継承（opus 以上推奨） | 条項リスクの見落としコストが大きい |
| ultron-freelance-rate-research | ピン sonnet | 公開統計の収集・レンジ整理 |
| ultron-company-research | ピン sonnet | 公開情報の収集・整理。ソース別の取得は research/haiku 委任可 |

### griot（練習・コーチング系）

| スキル | 指定 | 理由・委任 |
|---|---|---|
| griot-modeling-drill | ピン sonnet | 題材の生成（日次小課題 + 月次ドメイン課題）。正解を出さない設計 |
| griot-project-doc-reviewer | 継承（opus 以上推奨） | 観点照合の判定と見落としコストが本体。プレイブック読み込みは軽いが、判定・引用・新観点の発見はセッションモデルの質に依存 |

### vision（プライベート・人間関係系）

| スキル | 指定 | 理由 |
|---|---|---|
| vision-people-memory | ピン sonnet | 会話からの抽出と台帳追記 |

（業務側スキルの割り当ては onclimb-industries の personas/model-selection.md を参照）

## ステップ単位の委任パターン（スキル共通）

1. **集計・計算・検算・ファイル移動** → 決定論スクリプト（0 トークン。モデルに一切やらせない）
2. **広域コード探索** → codebase-reader / Explore サブエージェント（sonnet 固定済み）
3. **判断のない機械走査**（列挙・単純フォーマット変換・ログのふるい）→ haiku サブエージェント
4. **Web 収集のファンアウト**（1 件ずつの取得・要約）→ sonnet 委任。リンク収集だけなら haiku
5. **最終判定・統合・レビュー・壁打ち** → セッションモデル（opus / fable）

## 運用

- ピンの追加・解除は 1 回の不満で行わず、[`ideas/skill-feedback.md`](../ideas/skill-feedback.md) の
  レビュー手順に乗せて判断する（SKILL.md 書き換えの共通ルールと同じ）。
- 新規スキル作成時は、この方針表で「ピン sonnet か継承か」を決めてから frontmatter を書く。
