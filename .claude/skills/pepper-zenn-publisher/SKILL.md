---
name: pepper-zenn-publisher
description: >-
  完成した技術記事を Zenn へ公開する。cabin 直下に clone した knowledge-hub リポジトリ
  (正本は GitHub の onc-limb/knowledge-hub、Zenn 連携済み) の articles/ に記事を配置し、
  Zenn frontmatter の整形 → zenn preview での表示確認 → ユーザーの明示 OK →
  commit & push (= 公開) の順で進める。push が公開トリガーなので、ユーザーの明示的な
  承認なしに push しない。pepper-tech-article-drafter の公開前チェック未通過ドラフトは
  公開しない。「この記事を Zenn に公開して」「knowledge-hub に記事を追加して」
  「zenn preview で確認したい」「記事の公開状態を変えて」等の明示依頼時のみ起動
  (自動起動しない)。記事本文の執筆・添削は pepper-tech-article-drafter の領分。
model: sonnet
effort: medium
metadata:
  type: skill
  pairs_with: pepper-tech-article-drafter
  data_dir: <repo>/knowledge-hub
---

# zenn-publisher — Zenn 記事の公開 (push = 公開、承認必須)

完成した記事を Zenn 連携リポジトリ knowledge-hub に配置し、preview で確認してから
commit & push で公開する。**main への push がそのまま Zenn 公開のトリガー**なので、
push 前のユーザー承認を絶対に省略しない。

## データ配置

- 公開リポジトリ: 当リポジトリ直下 `knowledge-hub/`（独立した git リポジトリの clone。
  cabin の `.gitignore` で追跡除外。正本は `git@github.com:onc-limb/knowledge-hub.git`）
  - 無い・壊れているときは clone し直す:
    `git clone git@github.com:onc-limb/knowledge-hub.git knowledge-hub && cd knowledge-hub && pnpm install`
- 記事: `knowledge-hub/articles/<slug>.md`（1 記事 1 ファイル）
- テンプレート: `knowledge-hub/template/article.template.md`
- 入力（記事の完成稿）:
  - pepper-tech-article-drafter の出力 `articles/<YYYY-MM-DD>-<slug>/draft.md`
  - またはユーザーが直接指定するファイル

## published フラグの意味（knowledge-hub の運用）

| `published` | Zenn | ホームページ (onc-limb.com) |
|---|---|---|
| `true` | 公開 | 公開 |
| `false` | 非公開 | 公開 |

push しても `published: false` なら Zenn には出ない。「Zenn に公開」= `true` にして push。

## フロー

### 1. 入力の受け取りと公開可否の確認

- drafter の出力を渡された場合: 公開前チェックリストを全通過済みか確認する。
  未通過・不明なら公開に進まず、先に pepper-tech-article-drafter の公開前チェックへ差し戻す。
- 直接持ち込みの記事の場合: NG ワード辞書 `articles/_ng-words.txt` があれば grep で
  機械チェックし、加えて顧客名・案件名・社内 URL・API キー・個人名の混入を目視確認する。
  疑わしい記述はユーザーに確認するまで公開しない。

### 2. 作業前に最新化

knowledge-hub は他環境からも編集されうるため、作業前に必ず `git pull` する。
未コミットの変更が残っていたら内容をユーザーに報告してから進める。

### 3. Zenn frontmatter の整形と配置

- slug（ファイル名）: 半角英小文字・数字・`-`・`_` のみ、**12〜50 文字**（Zenn の制約）。
  既存記事の慣習に合わせ `<topic-slug>-onclimb.md` とする。
- frontmatter は template に従う: `title`（40 文字以内目安）/ `emoji`（1 つ）/
  `type`（`tech` or `idea`）/ `topics`（5 つ以内、半角英小文字）/ `published`。
- 初回配置時は `published: false` で置き、preview 確認後に `true` へ変えるのを既定とする
  （ユーザーが最初から `true` を指定した場合はそれに従う）。
- 本文は書き換えない。Zenn 記法として壊れている箇所（コードブロックの言語指定漏れ等）が
  あれば指摘し、修正はユーザーの了解を得てから行う。

### 4. preview で表示確認

```bash
cd knowledge-hub && pnpm exec zenn preview
```

`http://localhost:8000` をユーザーに案内し、表示確認してもらう。確認が取れるまで次へ進まない。
（preview はフォアグラウンドで動き続けるのでバックグラウンド実行にする。確認後に停止する）

### 5. 承認 → commit & push（= 公開）

1. `git status` と `git diff` の要点をユーザーに提示する。
2. **「push してよいか」を明示的に確認する**。承認が無い限り push しない。
3. 承認後: Conventional Commits でコミットし push する。
   - 例: `feat(articles): add go-build-2-onclimb` / `fix(articles): correct code sample in golang-di_onclimb`
4. push 後、Zenn への反映（数分かかることがある）と記事 URL の目安
   （`https://zenn.dev/<username>/articles/<slug>`）を伝える。

### 6. 公開状態の変更・修正も同じ流れ

既存記事の `published` 切替や本文修正も、最新化 → 変更 → preview → 承認 → push の同じ流れで行う。

## やらないこと

- 記事本文の執筆・構成提案・添削（pepper-tech-article-drafter / knowledge-hub の AGENTS.md 添削フローの領分）
- ユーザー承認なしの push・`published: true` 化
- knowledge-hub の依存更新（zenn-cli のバージョンアップ等）を公開作業のついでに行うこと
- `books/`・`knowledges/` の運用（依頼があれば同じ承認フローを適用する）
