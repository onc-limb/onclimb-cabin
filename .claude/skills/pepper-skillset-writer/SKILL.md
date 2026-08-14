---
name: pepper-skillset-writer
description: >-
  knowledge-base(技術ナレッジ vault)を主入力に、homepage に掲載するスキルセット紹介文を生成・更新する。技術ごとのスキルページ(経験・知識・レベル)の執筆と、プロフィールのスキルセット紹介文の整形を担い、capture-data の経験ノートや worklog の digest を補助入力に、根拠のある記述だけを公開向けに清書する。「homepage のスキルセットを書いて/更新して」「スキルページを埋めて」「NestJS のスキル紹介を書いて」等の明示依頼時のみ起動(自動起動しない)。ナレッジの蓄積自体は jarvis-knowledge-base、技術記事化は pepper-tech-article-drafter の領分。
model: sonnet
effort: medium
---

# skillset-writer — homepage スキルセット紹介文の生成

蓄積系スキルが作った一次情報を、**外部公開に耐えるスキルセット紹介文**へ整形する。
パイプラインの最終段（capture / worklog → knowledge-base → **本スキル** → homepage）。

## 入力（読む順）

入力の正本はすべて業務側リポジトリ onclimb-industries（既定 `~/Documents/onclimb-industries`）。
依存方向は「本スキル → 業務側のデータ」の読み取り一方向。

1. **knowledge-base vault**（主入力）: `$KB_HOME`（既定 `~/Documents/onclimb-industries/knowledge-base`）。
   `index.md`（技術領域の一覧）と `tech/<slug>.md`（領域ごとの知見・出典）。
2. **capture-data/tech/**（補助。`$CAPTURE_DATA_HOME` で上書き可）: 技術レベルの自己申告
   （何をしたか・どこまで理解しているか・まだやっていないこと・status）。**level 判定の主根拠**。
3. **capture-data/experience/**（補助）: 経験の文脈・エピソード。紹介文に厚みを足す材料。
4. **worklog-data/digests/**（補助。`$WORKLOG_DATA_HOME` で上書き可）: 実務での使用実績の裏付け。

## 出力先（homepage リポジトリ）

対象リポジトリは onclimb-industries 側の `projects/homepage/`
（既定 `~/Documents/onclimb-industries/projects/homepage`。無ければユーザーに場所を確認する）。
その構造に従う。**書き込み前に必ず対象ファイル一覧を提示して
確認を取る**（成果物のプロジェクトへの出力であり、スキルデータの保存ではない）。

- **技術別スキルページ**: `service/docs/skills/<category>/<tech>.md`。
  frontmatter（`name` / `category` / `level` / `publish`）＋ `## 経験` `## 知識` の箇条書き。
  **category の許容値と level の定義（1=学習中〜5=専門）は `service/lib/skills.ts` を実行時に
  読んで従う**（ハードコードされた記憶で書かない。既存ページの書きぶり・粒度にも合わせる）。
- **プロフィール紹介文**: `service/docs/profile.md` 等のスキルセット概説
  （対象ファイルは実行時にリポジトリ構造から特定し、ユーザーに確認する）。

## フロー

1. **対象の確定**: 全スキルページの棚卸しか、特定技術か、プロフィール文かを確認する。
   「記載中」プレースホルダのページ・publish: false のページを列挙して提案するのが既定。
2. **根拠の収集**: 対象技術ごとに、knowledge-base のノート → capture-data/tech →
   worklog digest の順で根拠を集める。**根拠の無い技術は書かない**（「見栄えのため」の水増し禁止）。
3. **level の提案**: capture の status・実務利用の証跡（worklog 出典の有無・期間）から
   level を提案し、必ずユーザーの確認を取る（自己申告より上げない）。
4. **ドラフト生成**: 既存の公開済みページ（publish: true）の粒度・文体に揃えて書く。
   経験 = 実際にやったこと（成果物・担当範囲）、知識 = 仕組み・概念の理解。
5. **公開前チェック（必須）**: 蓄積≠公開。以下を 1 周確認してから提示する:
   - 顧客名・案件特定情報・接続情報・`<REDACTED:..>` の残骸が無い（伏字はそのまま出さず文を書き直す）
   - ネガティブな他者評価・社内事情が混ざっていない
   - 経験の誇張が無い（capture の「まだやっていないこと」と矛盾する記述の禁止）
6. **清書パス**: [`personas/writing-style.md`](../../../personas/writing-style.md) を全面適用
   （公開文章）。
7. **確認 → 書き込み**: 変更ファイルと diff 要点を提示し、承認後に homepage へ書き込む。
   publish フラグの切り替えはユーザー判断（勝手に true にしない）。コミットはユーザーの指示があるまで行わない。

## 原則

- 入力（vault・capture・digest）に無い事実を創作しない。不足はヒアリングで埋めるか
  「根拠不足のため保留」として対象から外す。
- homepage 側のスキーマ（category enum・level 定義・frontmatter）を変える必要が出たら、
  勝手に変えず homepage 側の課題として提案する（依存方向: プロジェクト → 当リポジトリのデータ）。
- knowledge-base が古い場合は、先に jarvis-knowledge-base（onclimb-industries 側）の更新を提案する。
