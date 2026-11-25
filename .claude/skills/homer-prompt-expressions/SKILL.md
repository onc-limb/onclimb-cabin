---
name: homer-prompt-expressions
description: >-
  自分が Claude Code に入力した発言だけを会話ログから取り込み、英語にしたうえで似た言い回しを 1 つにまとめ、使用回数と日本語訳つきの「よく使う表現」一覧(english-notes/prompt-expressions/、git 管理外)を作る。サブエージェントへの依頼・ツール結果・ols が流し込む定型文・貼り付けは除外する。表現は単語ではなく 1 文単位。Anki 用 TSV(英から日の一方向)も出す。「プロンプトから英語表現を抽出して」「よく使う表現を更新して」「英語の表現集を作り直して」等の明示依頼時のみ起動(自動起動しない)。技術書の語彙メモ・1 日 1 文の産出練習は english-notes の手運用の領分。
model: sonnet
effort: low
metadata:
  type: skill
  data_dir: <repo>/english-notes/prompt-expressions
---

# prompt-expressions — 自分のプロンプトから「よく使う表現」を作る

Claude Code に**自分が入力した発言だけ**を集め、「同じことを英語で言うならこう言う」という表現に
変換して、似たものを 1 つにまとめて回数順に並べる。
persona: [`personas/homer.md`](../../../personas/homer.md)（読み手は自分・元の言葉を消さない）。

すべての操作は `scripts/prompt_expressions.py` 経由で行う（JSON を直接編集しない）。
cabin 単体で完結し、onclimb-industries のデータ・スキルは参照しない（入力は `~/.claude/projects` の会話ログ）。

## 何を「自分の発言」とみなすか

| 取り込む | 取り込まない |
|---|---|
| `origin.kind == "human"` の user メッセージ | `subagents/` 配下のログ・`isSidechain` / `isMeta` の行 |
| | ツール結果・システムの差し込み（`<system-reminder>` `<command-*>` `<task-notification>` 等） |
| | 貼り付け（`<pasted_content>`）・IDE の選択範囲（`<ide_selection>`） |
| | ols などが端末へ流し込む定型文（スクリプトの `NOISE_PATTERNS`） |
| | 日本語を含まない発言・4 文字未満の相づち |

- **Codex のログは対象外**。人の入力と ols board の通知が同じ形で記録されていて見分けられない。
- パス・URL・メールアドレス・トークン風の文字列は取り込み時に `<path>` などへ置き換える。
- 新しい定型文が混ざっていたら `NOISE_PATTERNS` に先頭一致の正規表現を足す（取り込み済みの分は
  `prompts.jsonl` から手で消さず、そのまま残してよい。気になるなら `extract` 前に相談）。

## 手順

```bash
S=.claude/skills/homer-prompt-expressions/scripts/prompt_expressions.py
python3 $S run                    # collect → extract → report を続けて実行
python3 $S collect                # 会話ログから新しい発言だけを取り込む（冪等）
python3 $S extract --max-batches 3   # 未処理の発言を 20 件ずつ claude -p（sonnet）に渡す
python3 $S merge                  # 似た表現をまとめ直す（まとめた側は言い換えとして残す）
python3 $S report                 # expressions.md / expressions.tsv を書き出す
python3 $S stats                  # 件数の確認
```

- `extract` は 1 バッチごとに保存するので、途中で止まっても続きから再開できる。
- 初回はまとめて処理する件数が多い（数百件）。Claude の利用枠を使うので、`--max-batches` で
  区切ってよい。`--dry-run` で LLM に渡す指示文を確認できる。
- 起動されたら `run` を実行し、`expressions.md` の上位 10 件と件数をユーザーに伝える。

## 出力（`english-notes/prompt-expressions/`）

| ファイル | 中身 |
|---|---|
| `prompts.jsonl` | 取り込んだ自分の発言（id・日時・プロジェクト名・本文） |
| `cursor.json` | 会話ログごとの取り込み済みバイト位置 |
| `expressions.json` | 表現カタログ（正本）。`en` / `ja`（日本語訳）/ `variants`（言い換え）/ `count`（使った発言の数）/ 期間 / 使用例 5 件 / `prompt_ids` |
| `expressions.*.bak.json` | `merge` 実行前のカタログの退避 |
| `expressions.md` | 回数順の一覧と、2 回以上使った表現の言い換え・使用例（元の日本語と英訳） |
| `expressions.tsv` | Anki 用（表 = 英語の表現 / 裏 = 訳・言い換え・使用例）。2 回以上のものだけ |

## まとめ方の決まり

- 1 表現は原則 1 文。複合した発言は言い回しごとに分ける。
- 話題に固有の名詞は X / Y に一般化し、"create a PR" "merge X into Y" のような動詞と目的語の組み合わせは残す。
- 核になる言い回しが同じなら、語尾の揺れや補足の違いがあっても同じ表現として数える。
  まとめる判断は LLM が既存カタログを見て行い、同じ英文の重複はスクリプトが正規化して吸収する。
- `count` は「その表現を使った発言の数」。1 つの発言で同じ表現が 2 回出ても 1 と数える。
- `extract` はバッチごとに既存カタログと照合するので、カタログが数百件を超えるとまとめ漏れが出る。
  まとまった量を `extract` したあとは `merge` を実行する。`merge` は 2 段で、骨格が同じ表現
  （同じ場面で同じことを伝え、代表の英文に置き換えても意味が変わらないもの）を 1 つにまとめる。
  - 段 1: 英文の順に並べて 200 件ずつ（`--window`）LLM に見せる。全体を一度に見せると LLM は
    目立つグループしか返さないため、区切って細かいまとめ漏れを拾う。
  - 段 2: 全体を 1 回見せて、書き出しが違うが同じことを言う表現（区切りをまたぐもの）を拾う。
  代表の英文は LLM が最も一般的な形を選び、元の英文は `variants`（言い換え）として回数つきで残す。
  動作の違い（push / merge / PR 作成）、作業の違い（教えて / 調べて / まとめて / 確認して）、
  機能の違い（依頼・質問・報告）、条件つきの型と単純な依頼は分けたままにする。迷ったら分ける。
- `merge` は実行前に `expressions.<日時>.bak.json` へカタログを退避する。結果が気に入らなければ
  退避ファイルを `expressions.json` に戻せばよい。

## 注意

- Claude Code は会話ログを一定期間（既定 30 日）で消すので、取り込めるのはその範囲だけ。
  ときどき `collect` だけでも実行しておくと取りこぼさない。
- 業務の発言も含まれるため、`english-notes/` は git 管理外のまま運用する。表現集を外に出すときは
  使用例（`examples`）に案件の固有名詞が残っていないか確認する。
