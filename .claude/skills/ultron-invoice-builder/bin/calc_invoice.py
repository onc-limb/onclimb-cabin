#!/usr/bin/env python3
"""calc_invoice.py — deterministic invoice amount calculator / verifier.

Reads line-item JSON and computes, with no LLM arithmetic involved:
  - per-line amounts (quantity x unit_price)
  - per-tax-rate subtotals and consumption tax (rounded ONCE per rate,
    as required for qualified invoices / 適格請求書)
  - withholding tax (源泉徴収): 10.21% on the base up to 1,000,000 JPY,
    20.42% on the portion exceeding 1,000,000 JPY
  - total billed and final amount due

All arithmetic uses Decimal (never float). Every figure is then re-verified
through an independent Fraction-based recomputation path; the result JSON
carries a `checks` section and the process exits non-zero if any check fails.

Usage:
  calc_invoice.py calc   --file items.json        # or --stdin
  calc_invoice.py verify --file items.json        # requires "expected" key

Input JSON:
{
  "line_items": [
    {"description": "SES engineering work (2026-06)",
     "quantity": 160.5, "unit": "h", "unit_price": 5000, "tax_rate": 10}
  ],
  "options": {                # all optional; defaults from config/defaults.json
    "withholding": true,      # apply withholding tax or not
    "rounding": "floor",      # floor | round | ceil (fraction-of-yen handling)
    "tax_rate_default": 10    # percent, used when a line omits tax_rate
  },
  "expected": {               # verify mode only: values to check against
    "subtotal": 802500, "tax_total": 80250,
    "withholding": 81935, "total_billed": 882750, "amount_due": 800815
  }
}

Only the Python standard library is used.
"""

import argparse
import json
import sys
from decimal import Decimal, InvalidOperation, ROUND_DOWN, ROUND_HALF_UP, ROUND_UP
from fractions import Fraction
from pathlib import Path

# Statutory withholding rates for payments to individuals (kept as constants
# on purpose: they are fixed by law, not tunable configuration).
WITHHOLDING_RATE = Decimal("0.1021")          # base <= threshold
WITHHOLDING_RATE_OVER = Decimal("0.2042")     # portion above threshold
WITHHOLDING_THRESHOLD = 1_000_000             # JPY

ROUNDING_MODES = {
    # ASSUMPTION: "floor" means truncation toward zero (round-down), which is
    # the common Japanese 切り捨て practice; negative (deduction) lines are
    # therefore truncated toward zero as well.
    "floor": ROUND_DOWN,
    "round": ROUND_HALF_UP,
    "ceil": ROUND_UP,
}

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "defaults.json"


class InputError(Exception):
    pass


def load_defaults():
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH, encoding="utf-8") as fh:
            return json.load(fh)
    return {}


def to_decimal(value, field):
    if isinstance(value, bool) or value is None:
        raise InputError(f"{field}: number required, got {value!r}")
    try:
        # Route through str() so JSON floats like 160.5 become exact decimals.
        return Decimal(str(value))
    except InvalidOperation as exc:
        raise InputError(f"{field}: cannot parse {value!r} as a number") from exc


def round_yen(value, mode):
    """Round a Decimal to integer yen using the configured mode."""
    return int(value.quantize(Decimal("1"), rounding=ROUNDING_MODES[mode]))


def round_fraction(frac, mode):
    """Independent integer rounding for the Fraction verification path."""
    sign = -1 if frac < 0 else 1
    q = abs(frac)
    whole = q.numerator // q.denominator
    rem = q - whole
    if mode == "floor":          # toward zero
        pass
    elif mode == "ceil":         # away from zero
        if rem > 0:
            whole += 1
    elif mode == "round":        # half away from zero
        if rem >= Fraction(1, 2):
            whole += 1
    else:
        raise InputError(f"unknown rounding mode: {mode}")
    return sign * whole


def fmt(n):
    return f"{n:,}"


def compute(data, defaults):
    options = dict(defaults)
    options.update(data.get("options") or {})
    rounding = options.get("rounding", "floor")
    if rounding not in ROUNDING_MODES:
        raise InputError(f"options.rounding must be one of {sorted(ROUNDING_MODES)}")
    withholding_enabled = bool(options.get("withholding", False))
    tax_rate_default = to_decimal(options.get("tax_rate_default", 10), "options.tax_rate_default")

    raw_items = data.get("line_items")
    if not isinstance(raw_items, list) or not raw_items:
        raise InputError("line_items: non-empty array required")

    items = []
    formulas = []
    for i, raw in enumerate(raw_items):
        field = f"line_items[{i}]"
        if not isinstance(raw, dict):
            raise InputError(f"{field}: object required")
        description = raw.get("description")
        if not description or not isinstance(description, str):
            raise InputError(f"{field}.description: non-empty string required")
        quantity = to_decimal(raw.get("quantity"), f"{field}.quantity")
        unit_price = to_decimal(raw.get("unit_price"), f"{field}.unit_price")
        tax_rate = to_decimal(raw.get("tax_rate", tax_rate_default), f"{field}.tax_rate")
        if tax_rate < 0 or tax_rate > 100:
            raise InputError(f"{field}.tax_rate: percent between 0 and 100 expected")
        amount = round_yen(quantity * unit_price, rounding)
        items.append({
            "description": description,
            "quantity": str(quantity),
            "unit": raw.get("unit", ""),
            "unit_price": str(unit_price),
            "tax_rate": str(tax_rate),
            "amount": amount,
        })
        formulas.append(
            f"{description}: {quantity} x {fmt_dec(unit_price)} = {fmt(amount)} JPY ({rounding})"
        )

    # Per-tax-rate grouping. Consumption tax is rounded ONCE per rate on the
    # grouped base (qualified-invoice rule: one rounding per rate, never per line).
    by_tax_rate = {}
    for item in items:
        rate_key = item["tax_rate"]
        group = by_tax_rate.setdefault(rate_key, {"taxable_base": 0, "tax": 0})
        group["taxable_base"] += item["amount"]
    for rate_key, group in by_tax_rate.items():
        rate = Decimal(rate_key)
        tax = round_yen(Decimal(group["taxable_base"]) * rate / Decimal(100), rounding)
        group["tax"] = tax
        formulas.append(
            f"consumption tax {rate_key}%: {fmt(group['taxable_base'])} x {rate_key}% = {fmt(tax)} JPY ({rounding})"
        )

    subtotal = sum(item["amount"] for item in items)
    tax_total = sum(group["tax"] for group in by_tax_rate.values())

    # Withholding tax. ASSUMPTION: the withholding base is the tax-exclusive
    # subtotal, which is permitted when consumption tax is itemized separately
    # on the invoice (as this skill always does).
    withholding = 0
    if withholding_enabled and subtotal > 0:
        base = Decimal(subtotal)
        if subtotal <= WITHHOLDING_THRESHOLD:
            withholding = round_yen(base * WITHHOLDING_RATE, "floor")
            formulas.append(
                f"withholding: {fmt(subtotal)} x 10.21% = {fmt(withholding)} JPY (floor)"
            )
        else:
            over = base - WITHHOLDING_THRESHOLD
            withholding = round_yen(
                Decimal(WITHHOLDING_THRESHOLD) * WITHHOLDING_RATE + over * WITHHOLDING_RATE_OVER,
                "floor",
            )
            formulas.append(
                f"withholding: 1,000,000 x 10.21% + {fmt_dec(over)} x 20.42% = {fmt(withholding)} JPY (floor)"
            )
    elif withholding_enabled:
        formulas.append("withholding: base <= 0, no withholding applied")
    else:
        formulas.append("withholding: disabled")

    total_billed = subtotal + tax_total
    amount_due = total_billed - withholding
    formulas.append(
        f"amount due: {fmt(subtotal)} + {fmt(tax_total)} - {fmt(withholding)} = {fmt(amount_due)} JPY"
    )

    result = {
        "line_items": items,
        "by_tax_rate": by_tax_rate,
        "subtotal": subtotal,
        "tax_total": tax_total,
        "withholding": withholding,
        "total_billed": total_billed,
        "amount_due": amount_due,
        "options": {
            "withholding": withholding_enabled,
            "rounding": rounding,
            "tax_rate_default": str(tax_rate_default),
        },
        "formulas": formulas,
    }
    result["checks"] = run_checks(raw_items, result, rounding, tax_rate_default, withholding_enabled)
    result["checks_passed"] = all(c["passed"] for c in result["checks"])
    return result


def fmt_dec(d):
    """Format a Decimal with thousands separators, keeping any fraction."""
    if d == d.to_integral_value():
        return f"{int(d):,}"
    return f"{d.normalize()}"


def run_checks(raw_items, result, rounding, tax_rate_default, withholding_enabled):
    """Recompute everything through an independent Fraction path and compare."""
    checks = []

    def check(name, expected, actual):
        checks.append({
            "name": name,
            "expected": expected,
            "actual": actual,
            "passed": expected == actual,
        })

    # Independent line amounts and per-rate bases.
    frac_amounts = []
    frac_bases = {}
    for raw in raw_items:
        qty = Fraction(str(raw["quantity"]))
        price = Fraction(str(raw["unit_price"]))
        rate = str(Decimal(str(raw.get("tax_rate", tax_rate_default))))
        amount = round_fraction(qty * price, rounding)
        frac_amounts.append(amount)
        frac_bases[rate] = frac_bases.get(rate, 0) + amount

    check("line_amounts", frac_amounts, [i["amount"] for i in result["line_items"]])
    check("subtotal", sum(frac_amounts), result["subtotal"])

    frac_tax_total = 0
    for rate_key, group in result["by_tax_rate"].items():
        base = frac_bases.get(rate_key, 0)
        tax = round_fraction(Fraction(base) * Fraction(str(rate_key)) / 100, rounding)
        frac_tax_total += tax
        check(f"taxable_base[{rate_key}%]", base, group["taxable_base"])
        check(f"tax[{rate_key}%]", tax, group["tax"])
    check("tax_total", frac_tax_total, result["tax_total"])

    subtotal = sum(frac_amounts)
    if withholding_enabled and subtotal > 0:
        if subtotal <= WITHHOLDING_THRESHOLD:
            wh = round_fraction(Fraction(subtotal) * Fraction("0.1021"), "floor")
        else:
            wh = round_fraction(
                Fraction(WITHHOLDING_THRESHOLD) * Fraction("0.1021")
                + Fraction(subtotal - WITHHOLDING_THRESHOLD) * Fraction("0.2042"),
                "floor",
            )
    else:
        wh = 0
    check("withholding", wh, result["withholding"])
    check("total_billed", subtotal + frac_tax_total, result["total_billed"])
    check("amount_due", subtotal + frac_tax_total - wh, result["amount_due"])
    return checks


VERIFY_KEYS = ("subtotal", "tax_total", "withholding", "total_billed", "amount_due")


def verify(data, defaults):
    expected = data.get("expected")
    if not isinstance(expected, dict) or not expected:
        raise InputError('verify mode requires an "expected" object '
                         f"with any of: {', '.join(VERIFY_KEYS)}")
    unknown = sorted(set(expected) - set(VERIFY_KEYS))
    if unknown:
        raise InputError(f"expected: unknown keys {unknown}; allowed: {list(VERIFY_KEYS)}")
    computed = compute(data, defaults)
    mismatches = []
    for key in VERIFY_KEYS:
        if key in expected and expected[key] != computed[key]:
            mismatches.append({
                "field": key,
                "expected": expected[key],
                "computed": computed[key],
            })
    return {
        "ok": not mismatches and computed["checks_passed"],
        "mismatches": mismatches,
        "computed": {key: computed[key] for key in VERIFY_KEYS},
        "checks_passed": computed["checks_passed"],
        "formulas": computed["formulas"],
    }


def read_input(args):
    if args.stdin:
        text = sys.stdin.read()
    elif args.file:
        with open(args.file, encoding="utf-8") as fh:
            text = fh.read()
    else:
        raise InputError("provide --file <path> or --stdin")
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise InputError(f"invalid JSON input: {exc}") from exc
    if not isinstance(data, dict):
        raise InputError("top-level JSON object required")
    return data


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=["calc", "verify"],
                        help="calc: compute amounts; verify: check claimed totals")
    parser.add_argument("--file", help="path to the input JSON file")
    parser.add_argument("--stdin", action="store_true", help="read input JSON from stdin")
    args = parser.parse_args(argv)

    try:
        data = read_input(args)
        defaults = load_defaults()
        if args.command == "calc":
            result = compute(data, defaults)
            ok = result["checks_passed"]
        else:
            result = verify(data, defaults)
            ok = result["ok"]
    except InputError as exc:
        json.dump({"error": str(exc)}, sys.stdout, ensure_ascii=False)
        print()
        return 2

    json.dump(result, sys.stdout, ensure_ascii=False, indent=2)
    print()
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
