#!/usr/bin/env python3
"""prompt_expressions.py — 自分が Claude Code に入力した発言から、英語学習用の「よく使う表現」を抽出する。

データは english-notes/prompt-expressions/（git 管理外）に置く。PROMPT_EXPRESSIONS_DIR で上書きできる。

対象は Claude Code の会話ログ（~/.claude/projects/**/*.jsonl、CLAUDE_PROJECTS_DIR で上書き可）のうち、
人間が入力した発言だけ:
  - origin.kind == "human" の user メッセージ（サブエージェントへの依頼・ツール結果・システム差し込みは除外）
  - subagents/ 配下と isSidechain のログは読まない
  - ols などが端末へ流し込む定型文（NOISE_PATTERNS）・貼り付け・IDE 選択範囲は除外
Codex のログは、人の入力と ols board の通知が同じ形で記録されていて見分けられないため対象外。

コマンド:
  collect   会話ログから自分の発言を取り込む（取り込み位置を cursor.json で管理。冪等）
  extract   未処理の発言を claude -p に渡し、表現を抽出して似た表現を 1 つにまとめる
  merge     似た表現をまとめ直し、まとめた側は言い換え（variants）として残す（実行前にバックアップを取る）
  report    expressions.md（一覧）と expressions.tsv（Anki 用・英→日）を書き出す
  run       collect → extract → report を続けて実行する
  stats     件数の確認
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[4]
DATA_DIR = Path(os.environ.get("PROMPT_EXPRESSIONS_DIR") or REPO / "english-notes" / "prompt-expressions")
SOURCE_DIR = Path(os.environ.get("CLAUDE_PROJECTS_DIR") or Path.home() / ".claude" / "projects")

PROMPTS = DATA_DIR / "prompts.jsonl"
CURSOR = DATA_DIR / "cursor.json"
CATALOG = DATA_DIR / "expressions.json"
REPORT_MD = DATA_DIR / "expressions.md"
REPORT_TSV = DATA_DIR / "expressions.tsv"

JST = timezone(timedelta(hours=9))
MAX_EXAMPLES = 5  # 1 表現あたり保持する使用例の数（新しい順）

# 発言から取り除くブロック（中身ごと削除）。貼り付け・IDE 選択は本人の言い回しではないため捨てる
STRIP_TAGS = (
    "pasted_content", "ide_selection", "ide_opened_file", "system-reminder",
    "command-name", "command-message", "command-args",
    "local-command-stdout", "local-command-stderr", "local-command-caveat",
    "task-notification", "bash-input", "bash-stdout", "bash-stderr",
)
STRIP_RE = re.compile(r"<(%s)\b[^>]*>.*?</\1>" % "|".join(re.escape(t) for t in STRIP_TAGS), re.S)

# origin.kind == human でも、実際は道具が端末へ流し込んだ定型文（先頭一致）
NOISE_PATTERNS = [re.compile(p) for p in (
    r"^\[ols board\]",
    r"^ols (inbox|handoff|board|task)\b",
    r"^担当タスクを確認して開始",
    r"^評価対象を確認して開始",
    r"^「?OK」?とだけ返して",
    r"^Reply with just\b",
)]

JA_CHAR = re.compile(r"[ぁ-んァ-ヶ一-龠]")

# 記録時のマスキング（パス・URL・メール・トークン風の文字列）
MASKS = [
    (re.compile(r"https?://\S+"), "<url>"),
    (re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+"), "<email>"),
    (re.compile(r"(?:~|/Users|/home|/private|/tmp|/opt|/var|/etc)/[^\s　、。，．）)」』\"'`]+"), "<path>"),
    (re.compile(r"\b(?:sk|ghp|gho|github_pat|xox[abp])[-_][A-Za-z0-9_\-]{16,}\b"), "<token>"),
    (re.compile(r"\b[A-Za-z0-9_\-]{40,}\b"), "<token>"),
]


# ---------------------------------------------------------------- 共通

def load_json(path: Path, default):
    if not path.exists():
        return default
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def save_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.replace(tmp, path)


def load_prompts() -> list[dict]:
    if not PROMPTS.exists():
        return []
    out = []
    with PROMPTS.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def jst_date(ts: str | None) -> str:
    if not ts:
        return ""
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(JST).strftime("%Y-%m-%d")
    except ValueError:
        return ts[:10]


def load_catalog() -> dict:
    return load_json(CATALOG, {"version": 1, "processed": [], "expressions": []})


# ---------------------------------------------------------------- collect

def message_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(i.get("text", "") for i in content if isinstance(i, dict) and i.get("type") == "text")
    return ""


def clean(text: str) -> str | None:
    """発言本文を学習用に整える。対象外なら None。"""
    t = STRIP_RE.sub("", text).strip()
    if not t or any(p.search(t) for p in NOISE_PATTERNS):
        return None
    if len(t) < 4 or not JA_CHAR.search(t):  # 短すぎる相づち・英語だけの疎通確認は除外
        return None
    for rx, repl in MASKS:
        t = rx.sub(repl, t)
    return t


def human_prompt(obj: dict) -> dict | None:
    if obj.get("type") != "user" or obj.get("isSidechain") or obj.get("isMeta"):
        return None
    if (obj.get("origin") or {}).get("kind") != "human":
        return None
    text = clean(message_text((obj.get("message") or {}).get("content")))
    if text is None:
        return None
    key = "%s:%s" % (obj.get("sessionId"), obj.get("uuid"))
    return {
        "id": hashlib.sha1(key.encode()).hexdigest()[:12],
        "ts": obj.get("timestamp"),
        "session": obj.get("sessionId"),
        "project": Path(obj.get("cwd") or "").name,
        "ja": text,
    }


def cmd_collect(args) -> int:
    cursor = load_json(CURSOR, {})
    known = {p["id"] for p in load_prompts()}
    new = []
    files = 0
    for path in sorted(SOURCE_DIR.rglob("*.jsonl")):
        if "subagents" in path.parts:
            continue
        key = str(path)
        size = path.stat().st_size
        offset = cursor.get(key, 0)
        if size == offset:
            continue
        if size < offset:  # 作り直されたファイルは頭から読む（id で重複は防ぐ）
            offset = 0
        files += 1
        with path.open("rb") as f:
            f.seek(offset)
            chunk = f.read()
        end = chunk.rfind(b"\n") + 1  # 書きかけの最終行は次回に回す
        for raw in chunk[:end].splitlines():
            if b'"human"' not in raw:
                continue
            try:
                obj = json.loads(raw)
            except json.JSONDecodeError:
                continue
            rec = human_prompt(obj)
            if rec and rec["id"] not in known:
                known.add(rec["id"])
                new.append(rec)
        cursor[key] = offset + end
    new.sort(key=lambda r: r["ts"] or "")
    if new:
        PROMPTS.parent.mkdir(parents=True, exist_ok=True)
        with PROMPTS.open("a", encoding="utf-8") as f:
            for r in new:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
    save_json(CURSOR, cursor)
    print("collect: 読んだファイル %d / 新しい発言 %d 件 / 累計 %d 件" % (files, len(new), len(known)))
    return 0


# ---------------------------------------------------------------- extract

SCHEMA = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "prompt_id": {"type": "string"},
                    "expressions": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "match_id": {"type": ["string", "null"]},
                                "en": {"type": "string"},
                                "ja": {"type": "string"},
                                "example_en": {"type": "string"},
                            },
                            "required": ["match_id", "en", "ja", "example_en"],
                        },
                    },
                },
                "required": ["prompt_id", "expressions"],
            },
        }
    },
    "required": ["items"],
}

INSTRUCTIONS = """\
あなたは英語教材の編集者です。以下は、日本人のソフトウェアエンジニアが AI アシスタント（Claude Code）に
日本語で入力した発言です。この人が「同じことを英語で言うならこう言う」と言えるようになるための教材を作ります。

## やること
各発言から、再利用できる「言い回し」を 0〜3 個抽出してください。

- 単語ではなく文の表現にする。1 表現は原則 1 文（目安 15 語以内）。どうしても切れないときだけ 2 文。
  「A して、B して」のような複合した発言は、言い回しごとに分割して別の表現にする。
- 依頼・指示・質問・確認・訂正・謝罪・前提の共有・方針の伝達など、話の中身ではなく
  「言い方」に再利用価値がある部分を選ぶ。
- en: ネイティブのエンジニアが AI アシスタントや同僚に実際に言う、自然な英語（直訳しない）。
  話題に固有の名詞は X / Y に置き換えて一般化する。ただし "create a PR" "cut a branch from X"
  "merge X into Y" のような、エンジニアがよく使う動詞と目的語の組み合わせは英語学習の価値があるので残す。
  例: 「まず実現方法の設計を考えて」→ "Could you start by designing how to do X?"
- 文体をそろえる: 依頼は "Could you ...?" か命令形のどちらか、元の発言の温度に近いほうを使う。
  「〜して」程度の素っ気ない依頼は命令形、「〜してほしい」「お願い」は "Could you ...?"。
- ja: en の自然な日本語訳（教材の「訳」欄に載る）。
- example_en: その発言そのものを英語にした文（1〜2 文）。<path> や <url> などの伏せ字はそのまま残す。
- 中身のない発言（相づち・「OK」・コマンド名だけ等）は expressions を空配列にする。

## 似た表現は 1 つにまとめる
- 既存カタログに、核になる言い回し（主な動詞と文の骨格）が同じ表現があれば、
  積極的に match_id にその id を入れる。無ければ null。
  例: "Sorry, I was working on other things. Please continue in a worktree." と
  "Sorry, I'm working on other things too, so handle this in a worktree." は同じ表現。
- 日本語の語尾の揺れ（「〜して」「〜してほしい」「〜をお願い」）、X の中身の違い、
  前後に付いた理由や補足の違いは同じ表現として扱う。
- 丁寧さ・機能（依頼か質問か、提案か命令か）がはっきり違うものは分ける。
- このバッチの中で新しく出た表現同士が同じものなら、en を一字一句同じ文字列にそろえる。

## 既存カタログ（id<TAB>代表の en | 言い換え ...）
{catalog}

## 発言（JSON 配列）
{prompts}

すべての発言について items に 1 件ずつ、prompt_id を入れて返してください。
"""


def norm(s: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", "", s.lower())).strip()


def call_claude(prompt: str, model: str, schema: dict = SCHEMA, timeout: int = 600) -> dict:
    cmd = [
        "claude", "-p", "--model", model, "--tools", "", "--no-session-persistence",
        "--setting-sources", "", "--output-format", "json", "--json-schema", json.dumps(schema),
    ]
    # cwd を空ディレクトリにして CLAUDE.md を読み込ませない。セッションも残さない（collect に混ざらない）
    with tempfile.TemporaryDirectory() as cwd:
        proc = subprocess.run(cmd, input=prompt, capture_output=True, text=True, cwd=cwd, timeout=timeout)
    if proc.returncode != 0:
        raise RuntimeError("claude -p failed (%d): %s" % (proc.returncode, proc.stderr.strip()[:500]))
    out = json.loads(proc.stdout)
    if out.get("is_error") or not isinstance(out.get("structured_output"), dict):
        raise RuntimeError("claude -p returned no structured output: %s" % str(out.get("result"))[:500])
    return out["structured_output"]


def next_id(exprs: list[dict]) -> str:
    # merge で欠番が出るので、件数ではなく最大の番号から振る
    return "e%04d" % (max((int(e["id"][1:]) for e in exprs), default=0) + 1)


def all_en(e: dict) -> list[str]:
    return [e["en"]] + [v["en"] for v in e.get("variants", [])]


def prompt_ids(e: dict) -> list[str]:
    # 旧データには prompt_ids が無いので使用例から復元する（count が 5 を超える分は不明のまま）
    return e.get("prompt_ids") or [x["prompt_id"] for x in e.get("examples", [])]


def apply_batch(catalog: dict, batch: list[dict], result: dict) -> tuple[int, int]:
    exprs = catalog["expressions"]
    by_id = {e["id"]: e for e in exprs}
    by_norm = {norm(en): e for e in exprs for en in all_en(e)}
    prompts = {p["id"]: p for p in batch}
    added = matched = 0
    for item in result.get("items", []):
        p = prompts.get(item.get("prompt_id"))
        if not p:
            continue
        date = jst_date(p["ts"])
        used = set()
        for x in item.get("expressions", []):
            en, ja = x.get("en", "").strip(), x.get("ja", "").strip()
            if not en:
                continue
            target = by_id.get(x.get("match_id") or "") or by_norm.get(norm(en))
            if target is None:
                target = {
                    "id": next_id(exprs), "en": en, "ja": ja, "variants": [],
                    "count": 0, "first_seen": date, "last_seen": date, "examples": [], "prompt_ids": [],
                }
                exprs.append(target)
                by_id[target["id"]] = target
                by_norm[norm(en)] = target
                added += 1
            else:
                matched += 1
            if target["id"] in used:  # 同じ発言の中で同じ表現は 1 回と数える
                continue
            used.add(target["id"])
            target["prompt_ids"] = prompt_ids(target) + [p["id"]]
            target["count"] += 1
            target["first_seen"] = min(filter(None, [target["first_seen"], date]), default=date)
            target["last_seen"] = max(filter(None, [target["last_seen"], date]), default=date)
            target["examples"].insert(0, {
                "prompt_id": p["id"], "date": date, "ja": p["ja"][:300], "en": x.get("example_en", "").strip(),
            })
            target["examples"] = sorted(target["examples"], key=lambda e: e["date"], reverse=True)[:MAX_EXAMPLES]
    return added, matched


def cmd_extract(args) -> int:
    catalog = load_catalog()
    done = set(catalog["processed"])
    pending = [p for p in load_prompts() if p["id"] not in done and jst_date(p["ts"]) >= (args.since or "")]
    if not pending:
        print("extract: 未処理の発言はありません")
        return 0
    batches = [pending[i:i + args.batch_size] for i in range(0, len(pending), args.batch_size)]
    if args.max_batches:
        batches = batches[:args.max_batches]
    print("extract: 未処理 %d 件 → 今回 %d バッチ（%d 件ずつ, model=%s）"
          % (len(pending), len(batches), args.batch_size, args.model))
    for n, batch in enumerate(batches, 1):
        listing = "\n".join(
            "%s\t%s" % (e["id"], " | ".join(all_en(e)[:4])) for e in catalog["expressions"]) or "（まだありません）"
        prompt = INSTRUCTIONS.format(
            catalog=listing,
            prompts=json.dumps([{"prompt_id": p["id"], "ja": p["ja"]} for p in batch], ensure_ascii=False, indent=1),
        )
        if args.dry_run:
            print(prompt)
            return 0
        try:
            result = call_claude(prompt, args.model)
        except (RuntimeError, subprocess.TimeoutExpired, json.JSONDecodeError) as e:
            print("extract: バッチ %d で失敗したので中断します（ここまでの結果は保存済み）: %s" % (n, e), file=sys.stderr)
            return 1
        added, matched = apply_batch(catalog, batch, result)
        returned = {i.get("prompt_id") for i in result.get("items", [])}
        missing = [p["id"] for p in batch if p["id"] not in returned]
        # 返ってこなかった発言は未処理のまま残し、次回もう一度渡す
        catalog["processed"].extend(p["id"] for p in batch if p["id"] in returned)
        save_json(CATALOG, catalog)
        print("  バッチ %d/%d: 新しい表現 %d / 既存にまとめた %d / 未返答 %d"
              % (n, len(batches), added, matched, len(missing)))
    print("extract: 表現 %d 種類" % len(catalog["expressions"]))
    return 0


# ---------------------------------------------------------------- merge

MERGE_SCHEMA = {
    "type": "object",
    "properties": {
        "groups": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "ids": {"type": "array", "items": {"type": "string"}},
                    "en": {"type": "string"},
                    "ja": {"type": "string"},
                },
                "required": ["ids", "en", "ja"],
            },
        }
    },
    "required": ["groups"],
}

MERGE_INSTRUCTIONS = """\
あなたは英語教材の編集者です。以下は、日本人のソフトウェアエンジニアが AI アシスタントに言った発言から
作った「英語の表現カタログ」です（1 行 = id<TAB>回数<TAB>英語<TAB>日本語訳）。
少しずつ追加してきたため、同じ言い回しが別々の項目に分かれています。教材として整理するため、
言い回しの骨格が同じものをグループにまとめてください。

## 1 つにまとめる条件
- 同じ場面で同じことを伝えていて、文の骨格（文の型と中心になる動詞）が同じか、
  代表の英文 1 つに置き換えても言っていることが変わらないもの。
  例: "Tell me the current state." / "How are things going right now?" / "Check the current status."
  （どれも「今の状況を教えて」）
  例: "Go ahead and proceed as is." / "Go ahead." / "This approach is fine."（どれも「この方針で進めて」の承認）
- 語尾の丁寧さの違い、X の中身の違い、前後に付いた理由や補足の違いは無視してよい。

## 分けておく条件
- 対象の動作が違う（push する / マージする / PR を作る は別）。
- 機能が違う（依頼・質問・報告・提案は別）。
- 中心の動詞が表す作業が違う。「教えて（tell / explain）」「調べて（investigate / look into）」
  「まとめて（summarize）」「確認して（check / verify）」は別の作業として分ける。
  例: "Tell me the current state." と "Investigate the current state." は別。
- 尋ねる対象の種類がはっきり違う。進み具合・状況を聞く表現と、設計や仕組みの説明を求める表現
  （"Tell me how X is designed."）は別。
- 迷ったら分けておく。まとめすぎると教材として使い分けが学べなくなる。
- 条件や順序を伝える型（"Once X, do Y." "Do X first, then Y."）と、条件のない単純な依頼は別。

## 返し方
- 2 件以上をまとめるグループだけを返す（まとめる相手がいない項目は返さない）。1 つの id は 1 グループにだけ入れる。
- en: グループの代表にする英文。ネイティブのエンジニアが最もよく使う自然で一般的な形にする
  （固有の名詞は X / Y）。メンバーの中から選んでも、より一般的な形に書き直してもよい。
- ja: en の自然な日本語訳。

## カタログ
{catalog}
"""


def merge_group(members: list[dict], en: str, ja: str) -> dict:
    rep = max(members, key=lambda e: (e["count"], -int(e["id"][1:])))
    ids, extras = [], 0
    for m in members:
        pids = prompt_ids(m)
        extras += max(0, m["count"] - len(pids))  # 使用例から復元できなかった分（重複は判定できない）
        ids += [i for i in pids if i not in ids]
    variants, seen = [], {norm(en)}
    candidates = [{"en": m["en"], "ja": m["ja"], "count": m["count"]} for m in members]
    candidates += [v for m in members for v in m.get("variants", [])]
    for v in sorted(candidates, key=lambda v: -v["count"]):
        if norm(v["en"]) not in seen:
            seen.add(norm(v["en"]))
            variants.append(v)
    examples = {x["prompt_id"]: x for m in members for x in m["examples"]}
    return {
        "id": rep["id"], "en": en, "ja": ja, "variants": variants,
        "count": len(ids) + extras,
        "first_seen": min(m["first_seen"] for m in members if m["first_seen"]),
        "last_seen": max(m["last_seen"] for m in members if m["last_seen"]),
        "examples": sorted(examples.values(), key=lambda x: x["date"], reverse=True)[:MAX_EXAMPLES],
        "prompt_ids": ids,
    }


def merge_pass(catalog: dict, subset: list[dict], model: str) -> int:
    """subset を LLM に見せてグループを受け取り、catalog に反映する。まとめたグループ数を返す。"""
    listing = "\n".join("%s\t%d\t%s\t%s" % (e["id"], e["count"], e["en"], e["ja"]) for e in subset)
    result = call_claude(MERGE_INSTRUCTIONS.format(catalog=listing), model, schema=MERGE_SCHEMA, timeout=1800)
    exprs = catalog["expressions"]
    by_id = {e["id"]: e for e in subset}
    taken, merged, removed = set(), {}, set()
    for g in result.get("groups", []):
        ids = [i for i in dict.fromkeys(g.get("ids", [])) if i in by_id and i not in taken]
        if len(ids) < 2 or not g.get("en", "").strip():
            continue
        taken.update(ids)
        new = merge_group([by_id[i] for i in ids], g["en"].strip(), g.get("ja", "").strip())
        merged[new["id"]] = new
        removed.update(i for i in ids if i != new["id"])
    catalog["expressions"] = [merged.get(e["id"], e) for e in exprs if e["id"] not in removed]
    return len(merged)


def cmd_merge(args) -> int:
    catalog = load_catalog()
    if len(catalog["expressions"]) < 2:
        print("merge: まとめる対象がありません")
        return 0
    if args.dry_run:
        subset = sorted(catalog["expressions"], key=lambda e: norm(e["en"]))[:args.window]
        print(MERGE_INSTRUCTIONS.format(catalog="\n".join(
            "%s\t%d\t%s\t%s" % (e["id"], e["count"], e["en"], e["ja"]) for e in subset)))
        return 0
    before = len(catalog["expressions"])
    backup = CATALOG.with_name("expressions.%s.bak.json" % datetime.now(JST).strftime("%Y%m%d-%H%M%S"))
    save_json(backup, catalog)
    print("merge: %d 種類（元のカタログは %s に退避, model=%s）" % (before, backup.name, args.model))

    # 段 1: 英文の順に並べて window 件ずつ。似た書き出しの表現が同じ区切りに入るので、細かいまとめ漏れを拾える
    ordered = sorted(catalog["expressions"], key=lambda e: norm(e["en"]))
    windows = [ordered[i:i + args.window] for i in range(0, len(ordered), args.window)] if args.window else []
    # 段 2: 全体を 1 回。書き出しが違うが同じことを言う表現（区切りをまたぐもの）を拾う
    passes = [("区切り %d/%d" % (n, len(windows)), w) for n, w in enumerate(windows, 1)]
    passes.append(("全体", None))
    for label, subset in passes:
        if subset is None:
            subset = sorted(catalog["expressions"], key=lambda e: norm(e["en"]))
        else:  # 前の段でまとめられて消えた id を除く
            alive = {e["id"]: e for e in catalog["expressions"]}
            subset = [alive[e["id"]] for e in subset if e["id"] in alive]
        n_before = len(catalog["expressions"])
        try:
            groups = merge_pass(catalog, subset, args.model)
        except (RuntimeError, subprocess.TimeoutExpired, json.JSONDecodeError) as e:
            print("merge: %s で失敗したので中断します（ここまでの結果は保存済み）: %s" % (label, e), file=sys.stderr)
            return 1
        save_json(CATALOG, catalog)
        print("  %s: %d グループ, %d 種類 → %d 種類" % (label, groups, n_before, len(catalog["expressions"])))
    print("merge: %d 種類 → %d 種類" % (before, len(catalog["expressions"])))
    return 0


# ---------------------------------------------------------------- report

def cell(s: str) -> str:
    return s.replace("|", "\\|").replace("\n", " ")


def cmd_report(args) -> int:
    catalog = load_catalog()
    # 回数の多い順、同数なら最近使ったものを先に
    exprs = sorted(catalog["expressions"], key=lambda e: e["last_seen"] or "", reverse=True)
    exprs.sort(key=lambda e: e["count"], reverse=True)
    total_uses = sum(e["count"] for e in exprs)
    now = datetime.now(JST).strftime("%Y-%m-%d %H:%M")
    lines = [
        "# よく使う表現 — 自分のプロンプトから",
        "",
        "Claude Code に自分が入力した発言を英語にし、似た言い回しを 1 つにまとめて回数順に並べたもの。",
        "`prompt_expressions.py report` が生成する（手で編集しない）。",
        "",
        "- 生成: %s" % now,
        "- 処理済みの発言: %d 件 / 表現: %d 種類 / 使用回数の合計: %d" % (len(catalog["processed"]), len(exprs), total_uses),
        "",
        "## 一覧",
        "",
        "| # | 回数 | English | 日本語訳 | 最後に使った日 |",
        "|---:|---:|---|---|---|",
    ]
    for i, e in enumerate(exprs, 1):
        lines.append("| %d | %d | %s | %s | %s |" % (i, e["count"], cell(e["en"]), cell(e["ja"]), e["last_seen"]))
    detail = [e for e in exprs if e["count"] >= args.detail_min_count]
    lines += ["", "## 使用例（%d 回以上）" % args.detail_min_count, ""]
    for i, e in enumerate(detail, 1):
        lines += ["### %d. %s（%d 回）" % (i, e["en"], e["count"]), "", "- 訳: %s" % e["ja"],
                  "- 期間: %s 〜 %s" % (e["first_seen"], e["last_seen"])]
        if e.get("variants"):
            lines.append("- 言い換え:")
            lines += ["  - %s — %s（%d 回）" % (v["en"], v["ja"], v["count"]) for v in e["variants"]]
        lines += ["- 使用例:"]
        for x in e["examples"]:
            lines += ["  - 元: %s" % x["ja"].replace("\n", " "), "    英: %s" % x["en"]]
        lines.append("")
    REPORT_MD.parent.mkdir(parents=True, exist_ok=True)
    REPORT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")

    # Anki 用（english-notes の運用に合わせて英 → 日の一方向。表=表現 / 裏=訳と使用例 / タグ）
    with REPORT_TSV.open("w", encoding="utf-8") as f:
        for e in exprs:
            if e["count"] < args.tsv_min_count:
                continue
            ex = e["examples"][0]["en"] if e["examples"] else ""
            back = e["ja"]
            if e.get("variants"):
                back += "<br><br>言い換え: " + " / ".join(v["en"] for v in e["variants"][:3])
            back += "<br><br>例: " + ex if ex else ""
            f.write("%s\t%s\t%s\n" % (e["en"].replace("\t", " "), back.replace("\t", " "), "prompt-expressions"))
    print("report: %s / %s（%d 種類）" % (REPORT_MD, REPORT_TSV, len(exprs)))
    return 0


# ---------------------------------------------------------------- stats / run

def cmd_stats(args) -> int:
    catalog = load_catalog()
    prompts = load_prompts()
    print("発言: %d 件（未処理 %d 件）/ 表現: %d 種類" % (
        len(prompts), len({p["id"] for p in prompts} - set(catalog["processed"])), len(catalog["expressions"])))
    return 0


def cmd_run(args) -> int:
    for step in (cmd_collect, cmd_extract, cmd_report):
        rc = step(args)
        if rc:
            return rc
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    def extract_opts(p):
        p.add_argument("--model", default="sonnet", help="claude -p に渡すモデル（既定: sonnet）")
        p.add_argument("--batch-size", type=int, default=20, help="1 回の呼び出しで渡す発言数（既定: 20）")
        p.add_argument("--max-batches", type=int, default=0, help="今回処理するバッチ数の上限（0 = 全部）")
        p.add_argument("--since", help="この日付（YYYY-MM-DD, JST）以降の発言だけを処理する")
        p.add_argument("--dry-run", action="store_true", help="最初のバッチのプロンプトを表示するだけ")

    def report_opts(p):
        p.add_argument("--detail-min-count", type=int, default=2, help="使用例を載せる最小回数（既定: 2）")
        p.add_argument("--tsv-min-count", type=int, default=2, help="Anki 用 TSV に出す最小回数（既定: 2）")

    sub.add_parser("collect").set_defaults(func=cmd_collect)
    p = sub.add_parser("merge")
    p.add_argument("--model", default="sonnet", help="claude -p に渡すモデル（既定: sonnet）")
    p.add_argument("--window", type=int, default=200,
                   help="段 1 で一度に見せる件数（英文順に区切る。0 で段 1 を省き全体だけ。既定: 200）")
    p.add_argument("--dry-run", action="store_true", help="段 1 の最初の区切りの指示文を表示するだけ")
    p.set_defaults(func=cmd_merge)
    p = sub.add_parser("extract")
    extract_opts(p)
    p.set_defaults(func=cmd_extract)
    p = sub.add_parser("report")
    report_opts(p)
    p.set_defaults(func=cmd_report)
    p = sub.add_parser("run")
    extract_opts(p)
    report_opts(p)
    p.set_defaults(func=cmd_run)
    sub.add_parser("stats").set_defaults(func=cmd_stats)

    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
