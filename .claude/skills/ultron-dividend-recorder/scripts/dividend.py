#!/usr/bin/env python3
"""dividend.py — 配当実績台帳 (records.json) の決定論的な操作ツール。

リポジトリ直下 dividend-data/records.json（git 管理外）を、スキーマ検証・重複検出・
検算付きで安全に操作する。personal-dashboard は DIVIDEND_RECORDS_PATH でこの台帳を
参照し、配当メトリクス (dividend-annual / dividend-cumulative) として取り込む。
Claude は金額の合算・検算を暗算せず、必ず本スクリプトの出力を転記する。

レコード形式は projects/personal-dashboard/shared/dividends.ts の DividendRecord に一致させる:
  { stockName, dividendPerShare, shares, amount, recordDate, sourceImage, extractedAt }

コマンド:
  add         1件追記（検証・重複チェック付き）
  import-csv  CSV 一括取り込み（Notion 過去データ移行用。--dry-run 対応）
  list        レコード一覧（--year / --stock でフィルタ）
  summary     年別・銘柄別の集計
  check       records.json 全体の整合チェック
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

# 既定の records.json: <repo>/dividend-data/records.json（git 管理外）
# 環境変数 DIVIDEND_RECORDS_PATH または --records で上書き可。
REPO_ROOT = Path(__file__).resolve().parents[4]
DEFAULT_RECORDS = REPO_ROOT / "dividend-data" / "records.json"

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
FIELDS = ["stockName", "dividendPerShare", "shares", "amount", "recordDate", "sourceImage", "extractedAt"]
# 一株配当 × 株数 と配当金額の許容差（端数処理ぶん）
CROSSCHECK_TOLERANCE = 1.0


def records_path(args: argparse.Namespace) -> Path:
    if getattr(args, "records", None):
        return Path(args.records)
    env = os.environ.get("DIVIDEND_RECORDS_PATH")
    if env:
        return Path(env)
    return DEFAULT_RECORDS


def load(path: Path) -> list[dict]:
    if not path.exists():
        return []
    raw = path.read_text(encoding="utf-8").strip()
    if not raw:
        return []
    data = json.loads(raw)
    if not isinstance(data, list):
        sys.exit(f"ERROR: 配当レコードの形式が不正です（配列ではありません）: {path}")
    return data


def save(records: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # saveDividendRecords (TS) と同じ形式: indent=2 + 末尾改行
    path.write_text(json.dumps(records, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def validate(rec: dict) -> list[str]:
    """1レコードの検証。致命的な問題のリストを返す（空なら OK）。"""
    problems = []
    if not str(rec.get("stockName", "")).strip():
        problems.append("stockName が空")
    if not DATE_RE.match(str(rec.get("recordDate", ""))):
        problems.append(f"recordDate が YYYY-MM-DD 形式ではない: {rec.get('recordDate')!r}")
    for key in ("dividendPerShare", "shares", "amount"):
        v = rec.get(key)
        if not isinstance(v, (int, float)) or v < 0:
            problems.append(f"{key} が 0 以上の数値ではない: {v!r}")
    return problems


def crosscheck(rec: dict) -> str | None:
    """一株配当 × 株数 ≒ 配当金額 の検算。ズレていれば警告文を返す。"""
    per, shares, amount = rec.get("dividendPerShare", 0), rec.get("shares", 0), rec.get("amount", 0)
    if per and shares and amount:
        expected = per * shares
        if abs(expected - amount) > CROSSCHECK_TOLERANCE:
            return f"検算不一致: {per} 円/株 × {shares} 株 = {expected:g} 円 ≠ 配当金額 {amount:g} 円"
    return None


def find_duplicates(rec: dict, existing: list[dict]) -> list[str]:
    """重複疑いの理由リストを返す。"""
    reasons = []
    src = str(rec.get("sourceImage", ""))
    for i, e in enumerate(existing):
        if (
            e.get("stockName") == rec.get("stockName")
            and e.get("recordDate") == rec.get("recordDate")
            and e.get("amount") == rec.get("amount")
        ):
            reasons.append(f"既存 #{i}: 同一の銘柄・基準日・金額 ({e.get('stockName')} {e.get('recordDate')} ¥{e.get('amount'):g})")
        elif src and e.get("sourceImage") == src:
            reasons.append(f"既存 #{i}: 同一の sourceImage ({src})")
    return reasons


def as_number(value: float) -> float | int:
    """整数値は int として保存する（TS 側の表示・JSON 表現と揃える）。"""
    return int(value) if value == int(value) else value


def build_record(args: argparse.Namespace) -> dict:
    return {
        "stockName": args.stock_name,
        "dividendPerShare": as_number(args.dividend_per_share),
        "shares": as_number(args.shares),
        "amount": as_number(args.amount),
        "recordDate": args.record_date,
        "sourceImage": args.source_image,
        "extractedAt": now_iso(),
    }


def cmd_add(args: argparse.Namespace) -> None:
    path = records_path(args)
    records = load(path)
    rec = build_record(args)

    problems = validate(rec)
    if problems:
        sys.exit("ERROR: " + " / ".join(problems))

    warn = crosscheck(rec)
    if warn:
        print(f"WARNING: {warn}")

    dups = find_duplicates(rec, records)
    if dups and not args.force:
        print("DUPLICATE: 重複疑いのため追記しませんでした。意図的なら --force を付けて再実行してください。")
        for d in dups:
            print(f"  - {d}")
        sys.exit(2)

    records.append(rec)
    save(records, path)
    print(f"ADDED: {rec['stockName']} ¥{rec['amount']:g} ({rec['recordDate']}) -> {path}")
    print(f"TOTAL: {len(records)} 件")


def cmd_import_csv(args: argparse.Namespace) -> None:
    """CSV 一括取り込み。列: stockName, recordDate, amount [, dividendPerShare, shares, sourceImage]"""
    path = records_path(args)
    records = load(path)
    added, skipped, errors = [], [], []

    with open(args.file, encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        required = {"stockName", "recordDate", "amount"}
        if reader.fieldnames is None or not required.issubset(set(reader.fieldnames)):
            sys.exit(f"ERROR: CSV に必須列 {sorted(required)} がありません。列: {reader.fieldnames}")
        for lineno, row in enumerate(reader, start=2):
            try:
                rec = {
                    "stockName": (row.get("stockName") or "").strip(),
                    "dividendPerShare": float(row.get("dividendPerShare") or 0),
                    "shares": float(row.get("shares") or 0),
                    "amount": float(row.get("amount") or 0),
                    "recordDate": (row.get("recordDate") or "").strip(),
                    "sourceImage": (row.get("sourceImage") or args.source).strip(),
                    "extractedAt": now_iso(),
                }
                for key in ("dividendPerShare", "shares", "amount"):
                    rec[key] = as_number(rec[key])
            except ValueError as e:
                errors.append(f"L{lineno}: 数値変換エラー: {e}")
                continue

            problems = validate(rec)
            if problems:
                errors.append(f"L{lineno}: " + " / ".join(problems))
                continue

            dups = find_duplicates(rec, records + added)
            if dups and not args.force:
                skipped.append(f"L{lineno}: {rec['stockName']} {rec['recordDate']} ¥{rec['amount']:g} ({dups[0]})")
                continue

            warn = crosscheck(rec)
            if warn:
                print(f"WARNING L{lineno}: {warn}")
            added.append(rec)

    print(f"取り込み対象: {len(added)} 件 / 重複スキップ: {len(skipped)} 件 / エラー: {len(errors)} 件")
    for s in skipped:
        print(f"  SKIP {s}")
    for e in errors:
        print(f"  ERROR {e}")

    if args.dry_run:
        print("DRY-RUN: 書き込みは行っていません。")
        for rec in added:
            print(f"  + {rec['recordDate']} {rec['stockName']} ¥{rec['amount']:g}")
        return

    if added:
        save(records + added, path)
        print(f"IMPORTED: {len(added)} 件を追記しました -> {path}")
        print(f"TOTAL: {len(records) + len(added)} 件")
    if errors:
        sys.exit(1)


def matches(rec: dict, args: argparse.Namespace) -> bool:
    if getattr(args, "year", None) and not str(rec.get("recordDate", "")).startswith(f"{args.year}-"):
        return False
    if getattr(args, "stock", None) and args.stock not in str(rec.get("stockName", "")):
        return False
    return True


def cmd_list(args: argparse.Namespace) -> None:
    records = [r for r in load(records_path(args)) if matches(r, args)]
    if not records:
        print("該当するレコードはありません。")
        return
    records.sort(key=lambda r: str(r.get("recordDate", "")))
    for i, r in enumerate(records):
        print(
            f"{i:3d}  {r.get('recordDate')}  {r.get('stockName')}  "
            f"¥{r.get('amount', 0):g}  ({r.get('dividendPerShare', 0):g}円/株 × {r.get('shares', 0):g}株)  "
            f"[{r.get('sourceImage', '')}]"
        )
    print(f"---\n{len(records)} 件")


def cmd_summary(args: argparse.Namespace) -> None:
    records = load(records_path(args))
    if not records:
        print("レコードがありません。")
        return

    by_year: dict[str, float] = {}
    by_stock: dict[str, float] = {}
    by_year_stock: dict[str, dict[str, float]] = {}
    for r in records:
        year = str(r.get("recordDate", ""))[:4] or "????"
        stock = str(r.get("stockName", "?"))
        amount = float(r.get("amount", 0))
        by_year[year] = by_year.get(year, 0) + amount
        by_stock[stock] = by_stock.get(stock, 0) + amount
        by_year_stock.setdefault(year, {})
        by_year_stock[year][stock] = by_year_stock[year].get(stock, 0) + amount

    total = sum(by_year.values())
    print(f"累計配当金額: ¥{total:,.0f}（{len(records)} 件）")
    print("\n■ 年別合計")
    for year in sorted(by_year):
        print(f"  {year}: ¥{by_year[year]:,.0f}")
    print("\n■ 銘柄別累計（降順）")
    for stock, amount in sorted(by_stock.items(), key=lambda kv: -kv[1]):
        print(f"  {stock}: ¥{amount:,.0f}")
    if getattr(args, "year", None):
        year = str(args.year)
        print(f"\n■ {year} 年の銘柄別内訳（降順）")
        for stock, amount in sorted(by_year_stock.get(year, {}).items(), key=lambda kv: -kv[1]):
            print(f"  {stock}: ¥{amount:,.0f}")


def cmd_check(args: argparse.Namespace) -> None:
    path = records_path(args)
    records = load(path)
    print(f"records: {path}（{len(records)} 件）")
    issues = 0
    for i, r in enumerate(records):
        unknown = set(r.keys()) - set(FIELDS)
        missing = set(FIELDS) - set(r.keys())
        problems = validate(r)
        warn = crosscheck(r)
        for msg in (
            [f"未知のフィールド: {sorted(unknown)}"] if unknown else []
        ) + (
            [f"欠損フィールド: {sorted(missing)}"] if missing else []
        ) + problems + ([warn] if warn else []):
            print(f"  #{i} {r.get('stockName', '?')} {r.get('recordDate', '?')}: {msg}")
            issues += 1
    print("OK: 問題は見つかりませんでした。" if issues == 0 else f"NG: {issues} 件の問題があります。")
    if issues:
        sys.exit(1)


def main() -> None:
    parser = argparse.ArgumentParser(description="配当実績台帳 (records.json) の操作ツール")
    parser.add_argument("--records", help="records.json のパス（既定: <repo>/dividend-data/records.json）")
    sub = parser.add_subparsers(dest="command", required=True)

    p_add = sub.add_parser("add", help="1件追記")
    p_add.add_argument("--stock-name", required=True)
    p_add.add_argument("--dividend-per-share", type=float, required=True, help="一株あたり配当（円）。不明なら 0")
    p_add.add_argument("--shares", type=float, required=True, help="保有株数。不明なら 0")
    p_add.add_argument("--amount", type=float, required=True, help="配当金額（円・税引前）")
    p_add.add_argument("--record-date", required=True, help="基準日 YYYY-MM-DD")
    p_add.add_argument("--source-image", required=True, help="抽出元画像のファイル名（移行データは notion-migration 等）")
    p_add.add_argument("--force", action="store_true", help="重複疑いでも追記する")

    p_imp = sub.add_parser("import-csv", help="CSV 一括取り込み（Notion 移行用）")
    p_imp.add_argument("--file", required=True, help="CSV パス。必須列: stockName,recordDate,amount")
    p_imp.add_argument("--source", default="notion-migration", help="sourceImage 列が無い行に入れる値")
    p_imp.add_argument("--dry-run", action="store_true")
    p_imp.add_argument("--force", action="store_true")

    p_list = sub.add_parser("list", help="レコード一覧")
    p_list.add_argument("--year", type=int)
    p_list.add_argument("--stock", help="銘柄名の部分一致")

    p_sum = sub.add_parser("summary", help="年別・銘柄別の集計")
    p_sum.add_argument("--year", type=int, help="この年の銘柄別内訳も表示")

    sub.add_parser("check", help="records.json 全体の整合チェック")

    args = parser.parse_args()
    {
        "add": cmd_add,
        "import-csv": cmd_import_csv,
        "list": cmd_list,
        "summary": cmd_summary,
        "check": cmd_check,
    }[args.command](args)


if __name__ == "__main__":
    main()
