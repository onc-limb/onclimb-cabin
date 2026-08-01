# Google Drive 連携セットアップ手順書 — ultron-family-budget-manager

作成日: 2026-07-03
対象: `shared-expense-data/` の `inbox/` と `archive/` を Google Drive で複数マシンに同期し、
常時起動の Mac Mini 上のスケジュール実行（`claude -p`）でレシート処理を自動化する。

## 実現可否の結論

**実現可能。** ただし Google Drive はファイル単位の同期であって排他制御（ロック）を持たないため、
**「書き込み処理は Mac Mini の 1 台だけ」という単一ライター運用を前提**にする（詳細は「運用ルール」）。

## 全体構成

```
[スマホ]           Google Drive アプリからレシート写真を inbox/ へ直接アップロード
[ノート PC 等]      inbox/ への投入と summary.md の閲覧のみ（処理はしない）
        │
        ▼  Google Drive (My Drive/家計レシート/)
        │      ├── inbox/      ← 未処理レシートの投入先（全端末から追加可）
        │      └── archive/    ← 処理済み画像 + transactions.jsonl + summary.md
        ▼
[Mac Mini (常時起動)]  launchd スケジュールで claude -p を実行し、
                      inbox → 抽出 → archive 移動 → jsonl 追記 → summary 再生成
                      （書き込みはこの 1 台だけ）
```

- 各 Mac では、リポジトリの `shared-expense-data/inbox` と `shared-expense-data/archive` を
  **Drive フォルダへのシンボリックリンク**にする。スキル・スクリプトはパスを変えずそのまま動く。
- `logs/` は同期しない（マシンローカル）。複数台が同じログファイルに追記すると競合コピーが
  できるため、ログは処理した端末に残す。
- 副次的な利点: スマホの Google Drive アプリから inbox に直接レシート写真を上げられるので、
  「撮って上げるだけ」の運用になる。

## 前提

- Google アカウント（家計用に夫婦で共有するアカウントでも、自分のアカウントの共有フォルダでも可）。
- Mac Mini に onclimb-industries リポジトリを clone 済みで、`claude` CLI にログイン済みであること。
- <!-- ASSUMPTION: Drive for Desktop の macOS マウント先は
  `~/Library/CloudStorage/GoogleDrive-<アカウント>/My Drive/`（ストリーミング時）。
  アプリの仕様変更でパスが変わっていたら、実際のパスに読み替える。 -->

---

## 手順 1. Google Drive デスクトップアプリの設定（各 Mac で実施）

1. [Google Drive for Desktop](https://www.google.com/drive/download/) をインストールし、対象アカウントでログイン。
2. 同期方式を確認する（設定 → Google Drive）:
   - **Mac Mini（処理する端末）**: **ミラーリング**を推奨。ファイル実体が常にローカルにあるため、
     無人実行時に「クラウドにしか実体が無いファイルを開けない」事故を避けられる。
     ストリーミングのままにする場合は、手順 2 のフォルダを右クリック →
     **「オフラインアクセスを許可」**を必ず設定する。
   - **その他の PC**: ストリーミングで可（閲覧と投入だけなので実体は必要時に落ちてくれば十分）。
3. マウント先パスを確認する:
   ```bash
   ls ~/Library/CloudStorage/    # GoogleDrive-<アカウント> があること（ストリーミング時）
   ```
   ミラーリング時は設定画面で指定したローカルフォルダが実体になる。以降 `<DRIVE>` と表記する
   （例: `~/Library/CloudStorage/GoogleDrive-xxx@gmail.com/My Drive`）。

## 手順 2. Drive 内にデータフォルダを作成し、既存データを移す（1 台からだけ実施）

現在データを持っているマシン（このリポジトリがある Mac）で行う。

```bash
REPO=/Users/satoshi-onga/Documents/onclimb-industries
DRIVE=<DRIVE>   # 手順 1-3 のパス

# Drive 側フォルダ（家計レシート/inbox・家計レシート/archive）は Web 上で作成済み（2026-07-03）。
# 既存の空フォルダに中身を移す。移動はコピーでなく mv（二重の正本を作らない）
mv "$REPO/shared-expense-data/inbox/"*   "$DRIVE/家計レシート/inbox/"
mv "$REPO/shared-expense-data/archive/"* "$DRIVE/家計レシート/archive/"
rm -f "$REPO/shared-expense-data/"{inbox,archive}/.DS_Store
rmdir "$REPO/shared-expense-data/inbox" "$REPO/shared-expense-data/archive"
```

アップロード完了（メニューバーの Drive アイコンが同期完了になる）まで待つ。
レシート画像が数百 MB あるため、初回同期には時間がかかる。

## 手順 3. シンボリックリンクを張る（リポジトリを置く各 Mac で実施）

```bash
REPO=/Users/satoshi-onga/Documents/onclimb-industries
DRIVE=<DRIVE>

mkdir -p "$REPO/shared-expense-data/logs"    # logs はローカルのまま
ln -s "$DRIVE/家計レシート/inbox"   "$REPO/shared-expense-data/inbox"
ln -s "$DRIVE/家計レシート/archive" "$REPO/shared-expense-data/archive"

# 確認: リンク先が見え、jsonl が読めること
ls -l "$REPO/shared-expense-data/"
python3 - <<'PY'
import json, glob
n = sum(1 for p in glob.glob("shared-expense-data/archive/*/transactions.jsonl")
          for l in open(p, encoding="utf-8") if l.strip())
print(f"transactions: {n} 件読めた")
PY
```

> 逆方向（Drive フォルダの中にシンボリックリンクを置いてローカルを指す）は
> Drive がリンク先を同期しないため使えない。**実体を Drive 側、リンクをリポジトリ側**に置くこと。

## 手順 4. Mac Mini のスケジュール実行（launchd + claude -p）

### 4-1. 権限設定（初回のみ）

ヘッドレス実行（`claude -p`）では権限プロンプトに答えられないため、Mac Mini の
リポジトリに許可ルールを置く。`$REPO/.claude/settings.local.json`（git 管理外）:

```json
{
  "permissions": {
    "allow": [
      "Read(//Users/<user>/Library/CloudStorage/**)",
      "Bash(ls *)",
      "Bash(mv *)",
      "Bash(mkdir *)",
      "Bash(python3 *)"
    ]
  }
}
```

<!-- ASSUMPTION: 許可ルールはこの粒度で足りる想定。初回は必ず対話セッションで
「レシート集計して」を実行し、不足している許可をプロンプトから確認して追記する。 -->

### 4-2. launchd ジョブの登録

`~/Library/LaunchAgents/com.onclimb.family-budget.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>com.onclimb.family-budget</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/zsh</string>
    <string>-lc</string>
    <string>cd /Users/<user>/Documents/onclimb-industries &amp;&amp; ~/.local/bin/claude -p "ultron-family-budget-manager スキルで shared-expense-data/inbox のレシートを処理して。inbox が空なら何もせず終了してよい。" >> shared-expense-data/logs/scheduled-run.log 2>&amp;1</string>
  </array>
  <key>StartCalendarInterval</key>
  <array>
    <dict><key>Hour</key><integer>8</integer><key>Minute</key><integer>0</integer></dict>
    <dict><key>Hour</key><integer>13</integer><key>Minute</key><integer>0</integer></dict>
    <dict><key>Hour</key><integer>21</integer><key>Minute</key><integer>0</integer></dict>
  </array>
</dict>
</plist>
```

- `<user>` と claude のパス（`which claude` で確認）は環境に合わせる。
- 頻度は 1 日 3 回（8/13/21 時）を初期値とする。レシートは即時性が不要なので十分。
- 登録と手動テスト:
  ```bash
  launchctl load ~/Library/LaunchAgents/com.onclimb.family-budget.plist
  launchctl start com.onclimb.family-budget    # その場で 1 回実行してログを確認
  tail -f ~/Documents/onclimb-industries/shared-expense-data/logs/scheduled-run.log
  ```

### 4-3. 動作確認（エンドツーエンド）

1. スマホまたは別 PC から、レシート画像 1 枚を Drive の `家計レシート/inbox/` に入れる。
2. Mac Mini で `launchctl start com.onclimb.family-budget` を手動実行する。
3. 確認: inbox から画像が消え、`archive/YYYY-MM/receipts/` にリネームされて入り、
   `transactions.jsonl` に 1 行増え、`summary.md` が更新されている。
4. 別 PC の Drive フォルダにも数分以内に同じ結果が反映されている。

---

## 運用ルール（重要 — これを破ると競合コピーができる）

1. **書き込み処理（レシート集計の実行）は Mac Mini の 1 台だけ**。
   他の PC ではスキルの取り込み処理を実行しない（閲覧と inbox への投入のみ）。
   別 PC で処理したい事情ができたら、Mac Mini の launchd を止めてから行う。
2. **inbox への追加は全端末・スマホから自由**。新規ファイルの追加は衝突しない
   （万一同名でも Drive が別名を付けるだけで、スキル側は source 重複チェックで弾く）。
3. `transactions.jsonl` / `summary.md` を手で直すときも Mac Mini 上で行うのが安全。
4. 「〇〇 のコピー」「(1)」等の**競合コピーを archive 内で見つけたら、正本 jsonl と
   突き合わせて手動で解消**する（jsonl の件数・合計と summary の一致確認は SKILL.md の
   Step 6 検算スニペットを使う）。

## トラブルシューティング / 注意

| 事象 | 原因と対処 |
|---|---|
| スケジュール実行がレシートを読めない（Read 失敗） | ストリーミングで実体未取得。Mac Mini をミラーリングにするか、`家計レシート/` を「オフラインアクセスを許可」にする |
| 同期直後の画像で読み取り失敗した | アップロード途中の不完全ファイルの可能性。スキルは失敗ファイルを inbox に残すので、次回実行で自然に回復する |
| `mv` が「クロスデバイス」的に遅い | リンク越しの移動は inbox → archive が同一 Drive 内なので通常は問題ない。遅い場合は Drive の同期状況を確認 |
| jsonl に競合コピーができた | 単一ライター違反が起きている。運用ルール 1 を再確認し、コピー側の差分行を正本へ手動マージして削除 |
| launchd ジョブが動かない | `launchctl list \| grep onclimb` で登録確認。ログ出力先の権限、claude のパス、`claude` のログイン状態（`claude -p "hi"` が単体で動くか）を確認 |
| Mac Mini がスリープしていた | 常時起動設定（システム設定 → エネルギー）と `caffeinate` の要否を確認 |

## この構成で「しないこと」

- Drive API / MCP 経由でのアップロード・ダウンロード実装（デスクトップアプリの同期に任せる。
  スキル側はローカルファイル操作のまま変更しない）。
- 双方向の同時書き込み（単一ライター原則で回避する）。
- `logs/` の同期（マシンローカル）。
