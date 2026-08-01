# CLAUDE.md

onclimb-cabin リポジトリのプロジェクト固有ガイドライン。

## リポジトリの位置づけ

**プライベート・勉強用**のスキル・データ置き場。私物 Mac でのみ使う。

- IT エンジニアとしての**業務**（案件・クライアント・コーディング・共有ドキュメント）は
  [onclimb-industries](../onclimb-industries/) の領分。案件参画で PC を貸与される場合、
  貸与 PC に入れるのは onclimb-industries、このリポジトリは入れない。
- 扱う領域: 個人・世帯の金融資産（配当・ポートフォリオ・家計）、フリーランスの事業事務
  （請求書・契約書・確定申告・単価相場）、個人の学習・練習（読書・モデリング）、人間関係の記録。
- **onclimb-industries に依存しない**（自己完結）。業務側のデータ・スキルを参照しない。
  稼働時間は別管理のモバイルアプリから受け取る（invoice-builder の入力）。

## スキル

スキルは `.claude/skills/` 配下に置き、**分類プレフィックス + スキル名**で命名する。
プレフィックスと分類の対応は [`.claude/skills/README.md`](.claude/skills/README.md) を参照。
各分類の共通ルール（役割・言語表記・原則・品質/安全性）は [`personas/`](personas/) にある。

新しくスキルを作るときは:

1. 分類を決め、対応する persona ファイル（`personas/<prefix>.md`）の共通ルールに従って設計する。
2. ディレクトリ名は `<prefix>-<skill-name>`、`SKILL.md` の `name:` フロントマターも同じ値にする。
3. frontmatter に `model` と `effort` を書く（基準は `personas/model-selection.md`）。
4. `description:` は `>-` の折り畳みブロックで書き、半角 `: ` と `#` を含めない
   （YAML が壊れて description が届かなくなる）。
5. `.claude/skills/README.md` のスキル一覧表を更新する。

## スキルのデータ置き場

- スキルのデータはリポジトリ直下の git 管理外ディレクトリ（例: `dividend-data/`,
  `shared-expense-data/`）に置き、`.gitignore` に理由コメント付きで追加する（既存の書式に合わせる）。
- 一部のデータディレクトリは Google Drive（マイドライブ/onclimb-industries 配下）への
  シンボリックリンク: `dividend-data`（ディレクトリ自体）、`people-memory/` と
  `shared-expense-data/` の中のサブディレクトリ。リンク切れのときは Google Drive アプリの起動を疑う。
- 台帳系（配当・家計・ToDo 等）は必ず各スキルの決定論スクリプト経由で操作し、JSON を直接編集しない。

## study ディレクトリ（AI がコードを書かない学習用の場）

`study/` は、ユーザーが勉強のために **AI（Claude）に絶対にコードを書かせない** ディレクトリ。

- **`study/` 配下ではコードを一切書かない・編集しない**（新規作成も含む）。ユーザー自身が書く。
- ユーザーからの質問には **言葉での解説・回答のみ** で応じる。方針・ヒント・間違いの指摘は
  日本語の説明で行い、コードそのもの（スニペット含む）は提示しない。
- 技術的担保として、PreToolUse フック `.claude/hooks/block-study-writes.py` が
  `Write`/`Edit`/`MultiEdit`/`NotebookEdit` によるこのディレクトリ配下への書き込みを拒否する。
- `study/` は `.gitignore` で git 管理外。

## モデル / effort のタスク別ルーティング

`.claude/agents/` に業務側と同じ 4 エージェント（quick / research / review / writer）を定義してある。
機械的な軽作業は `quick`（haiku）、調査は `research`（sonnet）、レビュー・難所は `review`（opus）、
文章作成は `writer`（sonnet）に委譲する。実装・ヒアリングが要る作業・数ファイルで済む確認は
メインの会話で進める。
