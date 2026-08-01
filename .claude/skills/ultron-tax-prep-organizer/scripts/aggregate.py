#!/usr/bin/env python3
"""aggregate.py — deterministic aggregation for ultron-tax-prep-organizer.

Reads classified transactions (CSV or JSON) and produces:
  - per-account totals (income / expense, with business-ratio applied amounts)
  - a needs-review list (unresolved classification, invalid values, suspected duplicates)

This script only ORGANIZES data. It is NOT tax advice. The fixed disclaimer is
embedded in every output so downstream consumers cannot drop it accidentally.

Standard library only (python3). No network access.

Input schema (CSV columns = JSON object keys, one row/object per transaction):
  date            YYYY-MM-DD (string)
  type            "income" | "expense" (also accepts 収入/売上/経費/支出)
  description     free text (摘要)
  amount          positive number in JPY (commas / yen signs tolerated)
  account         勘定科目 name, or "要確認" / empty when undecided
  business_ratio  0-100 (optional; default 100; user-confirmed values only)
  needs_review    true/false (optional; default false)
  review_reason   free text (optional)
  source          where the row came from (optional)

Deterministic review rules (rows matching any are EXCLUDED from totals and
listed in needs_review with reasons):
  1. needs_review is truthy
  2. account is empty or a review label ("要確認" etc.)
  3. amount is missing, non-numeric, or <= 0
  4. business_ratio is outside (0, 100]
  5. type is not income/expense
  6. suspected duplicate: same (date, type, amount, description) as an
     earlier row -> 2nd and later occurrences are flagged

Usage:
  python3 aggregate.py --file transactions.csv                 # JSON to stdout
  python3 aggregate.py --file transactions.json --markdown     # Markdown to stdout
  python3 aggregate.py --file tx.csv --out summary.json        # write to file
  cat tx.json | python3 aggregate.py --stdin --format json
"""

import argparse
import csv
import datetime
import io
import json
import sys
from collections import OrderedDict
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

DISCLAIMER = (
    "本出力は取引データの機械的な整理・集計であり、税務助言ではありません。"
    "勘定科目の最終判断・按分率の妥当性・申告内容の確定は、"
    "利用者本人または税理士が行ってください。要確認リストが空でない間は集計は未確定です。"
)

# Labels meaning "account not decided yet" (case-insensitive for ascii).
REVIEW_ACCOUNT_LABELS = {"", "要確認", "不明", "unknown", "review", "tbd"}

INCOME_TYPES = {"income", "収入", "売上", "売上高", "sales", "revenue"}
EXPENSE_TYPES = {"expense", "経費", "支出", "費用", "cost"}

TRUTHY = {"true", "1", "yes", "y", "はい", "要", "要確認", "x"}

# Common account names for a freelance engineer (blue-return / 青色申告).
# Used only for warnings on unknown names; unknown accounts are still
# aggregated under the given name. See references/account_mapping.md.
KNOWN_INCOME_ACCOUNTS = {"売上高", "雑収入", "家事消費"}
KNOWN_EXPENSE_ACCOUNTS = {
    "租税公課", "荷造運賃", "水道光熱費", "旅費交通費", "通信費",
    "広告宣伝費", "接待交際費", "損害保険料", "修繕費", "消耗品費",
    "減価償却費", "福利厚生費", "給料賃金", "外注工賃", "利子割引料",
    "地代家賃", "貸倒金", "雑費", "新聞図書費", "研修費", "支払手数料",
    "会議費", "車両費",
}


def parse_bool(value):
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in TRUTHY


def parse_amount(value):
    """Return Decimal amount or None if unparsable."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        try:
            return Decimal(str(value))
        except InvalidOperation:
            return None
    text = str(value).strip().replace(",", "").replace("¥", "").replace("円", "")
    if not text:
        return None
    try:
        return Decimal(text)
    except InvalidOperation:
        return None


def normalize_type(value):
    text = (str(value or "")).strip().lower()
    if text in INCOME_TYPES:
        return "income"
    if text in EXPENSE_TYPES:
        return "expense"
    return None


def yen(value):
    """Round a Decimal to integer yen (ROUND_HALF_UP) and return int."""
    return int(value.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def load_rows(text, fmt):
    """Parse input text as csv or json into a list of dicts."""
    if fmt == "json":
        data = json.loads(text)
        if isinstance(data, dict):
            data = data.get("transactions", [])
        if not isinstance(data, list):
            raise ValueError("JSON input must be an array of transactions")
        return [dict(row) for row in data]
    reader = csv.DictReader(io.StringIO(text))
    return [dict(row) for row in reader]


def detect_format(path, explicit):
    if explicit != "auto":
        return explicit
    if path and path.lower().endswith(".json"):
        return "json"
    if path and path.lower().endswith(".csv"):
        return "csv"
    return "csv"


def evaluate(rows):
    """Deterministically split rows into aggregated totals and a review list."""
    income_by_account = OrderedDict()
    expense_by_account = OrderedDict()
    needs_review = []
    warnings = []
    seen = {}

    income_total = Decimal(0)
    expense_total_applied = Decimal(0)
    review_pending_total = Decimal(0)
    aggregated_count = 0

    for index, raw in enumerate(rows, start=1):
        date = str(raw.get("date") or "").strip()
        description = str(raw.get("description") or "").strip()
        account = str(raw.get("account") or "").strip()
        source = str(raw.get("source") or "").strip()
        tx_type = normalize_type(raw.get("type"))
        amount = parse_amount(raw.get("amount"))

        ratio_raw = raw.get("business_ratio")
        if ratio_raw is None or str(ratio_raw).strip() == "":
            ratio = Decimal(100)
            ratio_invalid = False
        else:
            ratio = parse_amount(ratio_raw)
            ratio_invalid = ratio is None or not (Decimal(0) < ratio <= Decimal(100))

        reasons = []
        if parse_bool(raw.get("needs_review")):
            reason = str(raw.get("review_reason") or "").strip()
            reasons.append(reason or "分類要確認(入力で指定)")
        if account.lower() in REVIEW_ACCOUNT_LABELS:
            reasons.append("勘定科目が未確定")
        if amount is None or amount <= 0:
            reasons.append("金額が不正(正の数値でない)")
        if ratio_invalid:
            reasons.append("按分率(business_ratio)が0-100の範囲外")
        if tx_type is None:
            reasons.append("取引区分(income/expense)が不明")

        # Duplicate detection: 2nd+ occurrence of same key goes to review.
        dup_key = (date, tx_type, str(amount), description)
        if amount is not None and dup_key in seen:
            reasons.append(
                "重複疑い(同一の日付・区分・金額・摘要が %d 行目に存在)" % seen[dup_key]
            )
        else:
            seen[dup_key] = index

        if reasons:
            needs_review.append({
                "row": index,
                "date": date,
                "type": str(raw.get("type") or ""),
                "description": description,
                "amount": int(amount) if amount is not None and amount == amount.to_integral_value() else (float(amount) if amount is not None else None),
                "account": account,
                "source": source,
                "reasons": reasons,
            })
            if amount is not None and amount > 0:
                review_pending_total += amount
            continue

        # Aggregate (deterministic; review-free rows only).
        aggregated_count += 1
        if tx_type == "income":
            bucket = income_by_account.setdefault(
                account, {"count": 0, "total": Decimal(0)})
            bucket["count"] += 1
            bucket["total"] += amount
            income_total += amount
            if account not in KNOWN_INCOME_ACCOUNTS:
                warnings.append(
                    "収入科目「%s」は既定リストにない科目です(そのまま集計。references/account_mapping.md 参照)" % account)
        else:
            applied = amount * ratio / Decimal(100)
            bucket = expense_by_account.setdefault(
                account,
                {"count": 0, "total_raw": Decimal(0), "total_applied": Decimal(0)})
            bucket["count"] += 1
            bucket["total_raw"] += amount
            bucket["total_applied"] += applied
            expense_total_applied += applied
            if account not in KNOWN_EXPENSE_ACCOUNTS:
                warnings.append(
                    "経費科目「%s」は既定リストにない科目です(そのまま集計。references/account_mapping.md 参照)" % account)

    # De-duplicate warnings, keep order.
    warnings = list(OrderedDict.fromkeys(warnings))

    return {
        "disclaimer": DISCLAIMER,
        "generated_at": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
        "totals": {
            "income_total": yen(income_total),
            "expense_total_applied": yen(expense_total_applied),
            "review_pending_total": yen(review_pending_total),
        },
        "income_by_account": {
            name: {"count": b["count"], "total": yen(b["total"])}
            for name, b in income_by_account.items()
        },
        "expense_by_account": {
            name: {
                "count": b["count"],
                "total_raw": yen(b["total_raw"]),
                "total_applied": yen(b["total_applied"]),
            }
            for name, b in expense_by_account.items()
        },
        "needs_review": needs_review,
        "warnings": warnings,
        "counts": {
            "transactions": len(rows),
            "aggregated": aggregated_count,
            "needs_review": len(needs_review),
        },
    }


def fmt_yen(value):
    return "{:,} 円".format(value)


def render_markdown(result, input_label):
    lines = []
    counts = result["counts"]
    totals = result["totals"]
    lines.append("# 勘定科目別集計サマリ")
    lines.append("")
    lines.append("> **免責**: %s" % result["disclaimer"])
    lines.append("")
    lines.append("- 生成日時: %s / 入力: %s" % (result["generated_at"], input_label))
    lines.append("- 取引 %d 件中、集計 %d 件 / 要確認 %d 件" % (
        counts["transactions"], counts["aggregated"], counts["needs_review"]))
    lines.append("- 収入合計: %s / 経費合計(按分適用後): %s / 要確認(保留)合計: %s" % (
        fmt_yen(totals["income_total"]),
        fmt_yen(totals["expense_total_applied"]),
        fmt_yen(totals["review_pending_total"])))
    lines.append("")

    lines.append("## 収入(科目別)")
    lines.append("")
    if result["income_by_account"]:
        lines.append("| 勘定科目 | 件数 | 合計 |")
        lines.append("|---|---:|---:|")
        for name, b in result["income_by_account"].items():
            lines.append("| %s | %d | %s |" % (name, b["count"], fmt_yen(b["total"])))
    else:
        lines.append("(集計対象の収入なし)")
    lines.append("")

    lines.append("## 経費(科目別)")
    lines.append("")
    if result["expense_by_account"]:
        lines.append("| 勘定科目 | 件数 | 合計(按分前) | 合計(按分適用後) |")
        lines.append("|---|---:|---:|---:|")
        for name, b in result["expense_by_account"].items():
            lines.append("| %s | %d | %s | %s |" % (
                name, b["count"], fmt_yen(b["total_raw"]), fmt_yen(b["total_applied"])))
    else:
        lines.append("(集計対象の経費なし)")
    lines.append("")

    lines.append("## 要確認リスト(集計から除外・本人/税理士の確認待ち)")
    lines.append("")
    if result["needs_review"]:
        lines.append("| # | 日付 | 摘要 | 金額 | 科目(候補) | 理由 |")
        lines.append("|---:|---|---|---:|---|---|")
        for item in result["needs_review"]:
            amount = item["amount"]
            amount_text = fmt_yen(amount) if isinstance(amount, int) else str(amount or "不明")
            lines.append("| %d | %s | %s | %s | %s | %s |" % (
                item["row"], item["date"] or "-", item["description"] or "-",
                amount_text, item["account"] or "-", " / ".join(item["reasons"])))
        lines.append("")
        lines.append("要確認が 0 件になるまで、上記の科目別合計は確定値ではありません。")
    else:
        lines.append("(なし)")
    lines.append("")

    if result["warnings"]:
        lines.append("## 警告")
        lines.append("")
        for warning in result["warnings"]:
            lines.append("- %s" % warning)
        lines.append("")

    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Deterministic per-account aggregation for tax prep (NOT tax advice).")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--file", help="input CSV/JSON file of classified transactions")
    source.add_argument("--stdin", action="store_true", help="read input from stdin")
    parser.add_argument("--format", choices=["auto", "csv", "json"], default="auto",
                        help="input format (default: auto by file extension, csv for stdin)")
    parser.add_argument("--markdown", action="store_true",
                        help="emit a Markdown summary instead of JSON")
    parser.add_argument("--out", help="write output to this path instead of stdout")
    args = parser.parse_args(argv)

    if args.file:
        with open(args.file, encoding="utf-8-sig") as handle:
            text = handle.read()
        input_label = args.file
    else:
        text = sys.stdin.read()
        input_label = "(stdin)"

    fmt = detect_format(args.file, args.format)
    try:
        rows = load_rows(text, fmt)
    except (ValueError, json.JSONDecodeError) as exc:
        print("error: failed to parse input as %s: %s" % (fmt, exc), file=sys.stderr)
        return 2
    if not rows:
        print("error: no transactions found in input", file=sys.stderr)
        return 2

    result = evaluate(rows)
    if args.markdown:
        output = render_markdown(result, input_label)
    else:
        output = json.dumps(result, ensure_ascii=False, indent=2)

    if args.out:
        with open(args.out, "w", encoding="utf-8") as handle:
            handle.write(output + "\n")
        print("written: %s" % args.out)
    else:
        print(output)
    return 0


if __name__ == "__main__":
    sys.exit(main())
