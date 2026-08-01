#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""取得済み指標から健全性コア条件を決定論的に判定する。

数値の捏造を構造的に防ぐため、合否はこのスクリプトだけが決める。
LLM は各社のデータ（利回り/配当性向/配当推移/営業利益推移）をサイトから取得して
下記 JSON に整形する役割に専念し、判定そのものはここに委ねる。

入力 JSON（1 社 or 複数社の配列。時系列はすべて古い→新しい）:
  {
    "ticker": "1234", "name": "...",
    "yield": 4.3, "payout_ratio": 38.0, "equity_ratio": 55.0, "roe": 12.0,
    "dividend_history": [40, 42, 45, 48, 50],          # 直近 5 期
    "op_profit_history": [90, ..., 160],               # 直近 10 期
    "op_cf_history": [100, ..., 180],                  # 直近 10 期
    "revenue_history": [1000, 1100, 1150, 1200, 1300], # 直近 5 期
    "eps_history": [80, 85, 90, 100, 110],             # 直近 5 期
    "sources": ["https://...","https://..."]
  }

使い方:
  echo '<json>' | python3 judge.py --stdin
  python3 judge.py --file companies.json
  python3 judge.py --file c.json --yield-min 4.0 --payout-max 50 --equity-min 40
出力: 判定結果 JSON（passed / checks / reasons / insufficient）。
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hdss_lib as L  # noqa: E402


# CLI フラグ → 設定キーと型（ユーザー発話でしきい値を都度上書きするためのオーバーライド）
OVERRIDE_FLAGS = {
    "--yield-min": ("yield_min", float),
    "--payout-max": ("payout_max", float),
    "--dividend-periods": ("dividend_periods", int),
    "--allow-cuts": ("allow_dividend_cuts", int),
    "--op-profit-periods": ("op_profit_periods", int),
    "--op-cf-periods": ("op_cf_periods", int),
    "--revenue-periods": ("revenue_periods", int),
    "--allow-revenue-declines": ("allow_revenue_declines", int),
    "--eps-periods": ("eps_periods", int),
    "--allow-eps-declines": ("allow_eps_declines", int),
    "--equity-min": ("equity_ratio_min", float),
    "--dividend-cagr-min": ("dividend_cagr_min", float),
    "--eps-cagr-min": ("eps_cagr_min", float),
    "--roe-min": ("roe_min", float),
}


def main(argv):
    file_path = None
    use_stdin = False
    overrides = {}
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--file":
            i += 1
            file_path = argv[i] if i < len(argv) else None
        elif a == "--stdin":
            use_stdin = True
        elif a in OVERRIDE_FLAGS:
            key, cast = OVERRIDE_FLAGS[a]
            i += 1
            overrides[key] = cast(argv[i])
        i += 1

    if file_path:
        with open(file_path, "r", encoding="utf-8") as f:
            raw = f.read()
    elif use_stdin:
        raw = sys.stdin.read()
    else:
        sys.stderr.write(
            "使い方: judge.py (--file <json> | --stdin)"
            " [--yield-min N] [--payout-max N] [--equity-min N]"
            " [--dividend-periods N] [--allow-cuts N]"
            " [--op-profit-periods N] [--op-cf-periods N]"
            " [--revenue-periods N] [--allow-revenue-declines N]"
            " [--eps-periods N] [--allow-eps-declines N]"
            " [--dividend-cagr-min N] [--eps-cagr-min N] [--roe-min N]\n")
        return 2

    try:
        data = json.loads(raw)
    except Exception as e:
        print(json.dumps({"error": "JSON 解析失敗: %s" % e}, ensure_ascii=False))
        return 1

    cfg = L.load_config()
    cfg.update(overrides)

    companies = data if isinstance(data, list) else [data]
    results = [L.judge_company(c, cfg) for c in companies]

    passed = [r for r in results if r["passed"]]
    summary = {
        "thresholds": {
            "yield_min": L.config_value(cfg, "yield_min", 4.0),
            "payout_max": L.config_value(cfg, "payout_max", 50.0),
            "dividend_periods": L.config_value(cfg, "dividend_periods", 5),
            "allow_dividend_cuts": L.config_value(cfg, "allow_dividend_cuts", 0),
            "op_profit_periods": L.config_value(cfg, "op_profit_periods", 10),
            "op_cf_periods": L.config_value(cfg, "op_cf_periods", 10),
            "revenue_periods": L.config_value(cfg, "revenue_periods", 5),
            "allow_revenue_declines": L.config_value(cfg, "allow_revenue_declines", 1),
            "eps_periods": L.config_value(cfg, "eps_periods", 5),
            "allow_eps_declines": L.config_value(cfg, "allow_eps_declines", 1),
            "equity_ratio_min": L.config_value(cfg, "equity_ratio_min", 40.0),
            "dividend_cagr_min": L.config_value(cfg, "dividend_cagr_min", 5.0),
            "eps_cagr_min": L.config_value(cfg, "eps_cagr_min", 5.0),
            "roe_min": L.config_value(cfg, "roe_min", 8.0),
        },
        "judged": len(results),
        "passed": len(passed),
        "failed": len(results) - len(passed),
        "results": results,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
