#!/usr/bin/env python3
"""transactions.jsonl を正本として summary.md を再生成する。

使い方:
    python3 regen_summary.py <archive/YYYY-MM ディレクトリ>

設計意図:
- 集計は必ず jsonl 全件からフル再計算する。差分加算だと、後から手作業で
  レコードを直したときに合計がズレるため。
- summary.md は派生ビューなので毎回完全上書きする。ただし人間が書き込む
  「月次確定」セクションだけは既存ファイルから引き継いで消さない。
- 画像の再 OCR は一切しない。このスクリプトは jsonl しか読まない。
"""
import json
import os
import re
import sys
from collections import OrderedDict

CATEGORIES = ["食費", "日用品", "交通費", "外食", "その他"]


def yen(n: int) -> str:
    return "¥{:,}".format(n)


def load_records(jsonl_path: str):
    records = []
    with open(jsonl_path, encoding="utf-8") as f:
        for i, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError as e:
                # 壊れたデータを黙って握りつぶすと合計が狂う。停止して知らせる。
                raise SystemExit(
                    f"[ERROR] {jsonl_path}:{i} がパースできません: {e}\n"
                    f"  該当行: {line}\n"
                    f"  行を修正してから再実行してください。"
                )
    return records


def read_confirmation_block(summary_path: str) -> str:
    """既存 summary.md の「月次確定」本文を引き継ぐ。無ければ空テンプレート。"""
    default = "- 確定日: -\n- Notion 登録: -"
    if not os.path.exists(summary_path):
        return default
    old = open(summary_path, encoding="utf-8").read()
    m = re.search(r"## 月次確定\n(.*?)\n\n---", old, re.S)
    # strip() で前後の空行を除去（再生成を繰り返しても空行が増えないように）
    return m.group(1).strip() if m else default


def build_summary(records, ym: str, confirmation: str, generated_at: str) -> str:
    # 明細は date 昇順、同日内は added_at 昇順
    records = sorted(records, key=lambda r: (r["date"], r.get("added_at", "")))
    n = len(records)

    totals = OrderedDict((c, 0) for c in CATEGORIES)
    counts = OrderedDict((c, 0) for c in CATEGORIES)
    for r in records:
        cat = r["category"] if r["category"] in CATEGORIES else "その他"
        totals[cat] += r["total"]
        counts[cat] += 1
    grand_total = sum(totals.values())
    grand_count = sum(counts.values())

    lines = [
        f"# {ym} サマリ",
        "",
        "> このファイルは `transactions.jsonl` から自動生成される。直接編集しないこと。",
        "> 内容を修正したい場合は `transactions.jsonl` を編集してから再生成する。",
        "",
        f"## 明細 ({n} 件)",
        "",
        "| 日付 | 店舗 | カテゴリ | 金額 | ファイル |",
        "|------|------|----------|------|----------|",
    ]
    for r in records:
        lines.append(
            f"| {r['date']} | {r['store']} | {r['category']} | "
            f"{yen(r['total'])} | {r['file']} |"
        )

    lines += ["", "## カテゴリ別合計", "", "| カテゴリ | 件数 | 金額 |", "|----------|------|------|"]
    for c in CATEGORIES:
        lines.append(f"| {c} | {counts[c]} | {yen(totals[c])} |")
    lines.append(f"| **合計** | **{grand_count}** | **{yen(grand_total)}** |")

    lines += [
        "",
        "## 月次確定",
        "",
        confirmation,
        "",
        "---",
        f"最終更新: {generated_at} (transactions.jsonl の {n} 件から生成)",
        "",
    ]
    return "\n".join(lines)


def main():
    if len(sys.argv) != 2:
        raise SystemExit("使い方: python3 regen_summary.py <archive/YYYY-MM ディレクトリ>")
    month_dir = sys.argv[1].rstrip("/")
    ym = os.path.basename(month_dir)
    jsonl_path = os.path.join(month_dir, "transactions.jsonl")
    summary_path = os.path.join(month_dir, "summary.md")

    if not os.path.exists(jsonl_path):
        raise SystemExit(f"[ERROR] {jsonl_path} が見つかりません")

    # 生成時刻は JST 固定 (家計簿は日本ローカル運用のため)
    try:
        from datetime import datetime, timezone, timedelta
        generated_at = datetime.now(timezone(timedelta(hours=9))).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        generated_at = "-"

    records = load_records(jsonl_path)
    confirmation = read_confirmation_block(summary_path)
    content = build_summary(records, ym, confirmation, generated_at)
    with open(summary_path, "w", encoding="utf-8") as f:
        f.write(content)

    grand = sum(r["total"] for r in records)
    print(f"{ym}: {len(records)} 件 合計{yen(grand)} → {summary_path}")


if __name__ == "__main__":
    main()
