#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""high-dividend-stock-screener 共通ライブラリ（依存ゼロ）。

役割:
  - データ置き場（台帳 / リスト / EDINET キャッシュ）のパス解決
  - 設定（screener.yaml）のロード（最小 YAML サブセットパーサ）
  - 調査済み台帳（JSONL）の読み書き
  - 銘柄区分の除外判定（REIT / 投資法人 / インフラファンド 等）
  - 健全性コア条件の決定論的判定

設計方針:
  worklog / knowledge-base スキルと同じリポジトリに同居する前提だが、
  cross-skill 結合を避けるため他スキルの lib には依存しない（必要最小限を自前で持つ）。
  数値の捏造を構造的に防ぐため、「判定」はこのスクリプト側で決定論的に行い、
  LLM は「取得・名寄せ・レビュー講評」に専念する（SKILL.md 参照）。
"""
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone

JST = timezone(timedelta(hours=9))


# ---------------------------------------------------------------------------
# パス解決
# ---------------------------------------------------------------------------

def skill_root():
    """このスクリプト群が入るスキルディレクトリ(=bin/ の親)。config/ references/ の場所。"""
    return os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def find_repo_root(start):
    d = os.path.abspath(start)
    for _ in range(60):
        if os.path.isdir(os.path.join(d, ".git")):
            return d
        parent = os.path.dirname(d)
        if parent == d:
            break
        d = parent
    return None


def data_home():
    """台帳・リスト・EDINET キャッシュを置く場所。
    優先: 環境変数 STOCK_DATA
          → スキルが属する git リポジトリ直下の stock-data/
          → ~/stock-data
    コード/設定とは分離し、生成データをスキルディレクトリの外に置く（worklog の方式を踏襲）。"""
    env = os.environ.get("STOCK_DATA")
    if env:
        return os.path.abspath(os.path.expanduser(env))
    repo = find_repo_root(skill_root())
    if repo:
        return os.path.join(repo, "stock-data")
    return os.path.expanduser("~/stock-data")


def registry_path():
    return os.path.join(data_home(), "registry", "screened.jsonl")


def lists_dir():
    return os.path.join(data_home(), "lists")


def edinet_dir():
    return os.path.join(data_home(), "edinet")


def references_dir():
    return os.path.join(skill_root(), "references")


def templates_dir():
    return os.path.join(skill_root(), "templates")


def ensure_dir(path):
    os.makedirs(path, exist_ok=True)
    return path


def today_jst():
    # Python スクリプトなので datetime.now は利用可（Workflow JS の制約とは無関係）
    return datetime.now(JST).strftime("%Y-%m-%d")


# ---------------------------------------------------------------------------
# 最小 YAML サブセットパーサ（screener.yaml 用）
# 対応: ブロックスタイルのマップ / シーケンス / スカラ。フロー記法・複数行スカラは未対応。
# ---------------------------------------------------------------------------

def _strip_comment(s):
    out = []
    q = None
    prev = " "
    for c in s:
        if q:
            out.append(c)
            if c == q:
                q = None
        else:
            if c in ('"', "'"):
                q = c
                out.append(c)
            elif c == "#" and prev in (" ", "\t"):
                break
            else:
                out.append(c)
        prev = c
    return "".join(out).rstrip()


def _parse_scalar(s):
    s = s.strip()
    if s == "":
        return None
    if len(s) >= 2 and s[0] == '"' and s[-1] == '"':
        return s[1:-1]
    if len(s) >= 2 and s[0] == "'" and s[-1] == "'":
        return s[1:-1]
    low = s.lower()
    if low in ("null", "~"):
        return None
    if low == "true":
        return True
    if low == "false":
        return False
    if re.fullmatch(r"-?\d+", s):
        return int(s)
    if re.fullmatch(r"-?\d+\.\d+", s):
        return float(s)
    return s


def _indent_of(line):
    return len(line) - len(line.lstrip(" "))


def _prepare_lines(text):
    out = []
    for raw in text.splitlines():
        stripped = _strip_comment(raw)
        if stripped.strip() == "":
            continue
        out.append((_indent_of(stripped), stripped.strip()))
    return out


def _parse_block(lines, i):
    if i >= len(lines):
        return None, i
    _, content = lines[i]
    if content == "-" or content.startswith("- "):
        return _parse_seq(lines, i, lines[i][0])
    return _parse_map(lines, i, lines[i][0])


def _parse_map(lines, i, indent):
    d = {}
    while i < len(lines):
        ci, content = lines[i]
        if ci != indent:
            break
        key, sep, rest = content.partition(":")
        if sep == "":
            break
        key = key.strip()
        rest = rest.strip()
        if rest == "":
            if i + 1 < len(lines) and lines[i + 1][0] > indent:
                val, i = _parse_block(lines, i + 1)
            else:
                val, i = None, i + 1
            d[key] = val
        else:
            d[key] = _parse_scalar(rest)
            i += 1
    return d, i


def _parse_seq(lines, i, indent):
    arr = []
    while i < len(lines):
        ci, content = lines[i]
        if ci != indent or not (content == "-" or content.startswith("- ")):
            break
        item = content[1:].strip()
        if item == "":
            if i + 1 < len(lines) and lines[i + 1][0] > indent:
                val, i = _parse_block(lines, i + 1)
            else:
                val, i = None, i + 1
            arr.append(val)
        else:
            arr.append(_parse_scalar(item))
            i += 1
    return arr, i


def yaml_load(text):
    lines = _prepare_lines(text)
    if not lines:
        return {}
    val, _ = _parse_block(lines, 0)
    return val


def load_config():
    path = os.path.join(skill_root(), "config", "screener.yaml")
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        cfg = yaml_load(f.read())
    return cfg if isinstance(cfg, dict) else {}


def config_value(cfg, key, default):
    v = cfg.get(key)
    return default if v is None else v


# ---------------------------------------------------------------------------
# 証券コードの正規化
# ---------------------------------------------------------------------------

def normalize_ticker(code):
    """証券コードを 4 桁(英数)の正規形へ。
    EDINET は末尾 0 付きの 5 桁(例 トヨタ 72030 / 新形式 130A0)で配布されるため、
    5 桁なら末尾 1 文字を落として 4 桁にそろえる。Yahoo 等の 4 桁はそのまま。

    制限: 末尾が 0 以外の 5 桁コード(優先株等。例 伊藤園第1種優先 25935)は
    4 桁へ正規化できないためそのまま返す = EDINET との突合対象外(SKILL.md §注意 参照)。
    """
    if code is None:
        return None
    s = str(code).strip().upper()
    if s == "":
        return None
    s = re.sub(r"\s", "", s)
    if len(s) == 5 and s.endswith("0"):
        return s[:4]
    return s


# ---------------------------------------------------------------------------
# 銘柄区分の除外判定（REIT / 投資法人 / インフラファンド 等）
# ---------------------------------------------------------------------------

DEFAULT_EXCLUDE_NAME_PATTERNS = [
    "投資法人",
    "リート",
    "ＲＥＩＴ",
    "REIT",
    "インフラ投資",
    "インフラファンド",
    "ETF",
    "ＥＴＦ",
    "上場投信",
    "ETN",
]


def _pattern_matches(name, upper, p):
    """1 パターンの照合。「リート」だけは部分一致だと「日本コンクリート工業」等を
    誤除外するため（実測事例。screening_rules.md §進化メモ 参照）、
    名称末尾が「リート」または「リート投資法人」を含む場合のみ一致とする。"""
    if p == "リート":
        return name.endswith("リート") or "リート投資法人" in name
    return p in name or p.upper() in upper


def exclusion_reason(name, exclude_patterns=None):
    """社名から除外対象(REIT 等)かどうかを判定。除外なら理由文字列、対象外なら None。
    名称サフィックス/部分一致での一次判定。市場区分・銘柄種別が取れる場合は呼び出し側で併用する。"""
    if not name:
        return None
    pats = exclude_patterns if exclude_patterns is not None else DEFAULT_EXCLUDE_NAME_PATTERNS
    upper = name.upper()
    for p in pats:
        if not p:
            continue
        if _pattern_matches(name, upper, p):
            return "name_match:%s" % p
    return None


# ---------------------------------------------------------------------------
# 調査済み台帳（JSONL, 1 社 1 行, 一意キー=法人番号）
# ---------------------------------------------------------------------------

def read_registry():
    """台帳を読み込み record の list を返す。無ければ空 list。壊れた行はスキップ。"""
    path = registry_path()
    if not os.path.exists(path):
        return []
    out = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except Exception:
                sys.stderr.write("[hdss] 台帳の壊れた行をスキップ: %s\n" % line[:80])
    return out


def registry_keys(records):
    """重複排除に使うキー集合(法人番号 と 証券コード)を返す。"""
    corp = set()
    tickers = set()
    for r in records:
        cn = r.get("corp_number")
        if cn:
            corp.add(str(cn))
        tk = normalize_ticker(r.get("ticker"))
        if tk:
            tickers.add(tk)
    return corp, tickers


def append_registry(record):
    """1 件追記。corp_number か ticker が既存なら追記せず False を返す（重複排除）。"""
    return append_registry_many([record])[0]


def append_registry_many(records_in):
    """複数件をまとめて追記。台帳の全読は 1 回だけ行い、追記した分もキー集合に
    加えながら重複排除する（1 件ずつ append_registry を呼ぶ O(n^2) を避ける）。
    入力と同順の bool リスト（追記したら True、重複スキップなら False）を返す。"""
    existing = read_registry()
    corp, tickers = registry_keys(existing)
    to_write = []
    results = []
    for record in records_in:
        cn = record.get("corp_number")
        tk = normalize_ticker(record.get("ticker"))
        if (cn and str(cn) in corp) or (tk and tk in tickers):
            results.append(False)
            continue
        if cn:
            corp.add(str(cn))
        if tk:
            tickers.add(tk)
        to_write.append(record)
        results.append(True)
    if to_write:
        path = registry_path()
        ensure_dir(os.path.dirname(path))
        with open(path, "a", encoding="utf-8") as f:
            for record in to_write:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
    return results


def update_registry(record):
    """既存 1 件を置換する（再検証・mode=new の再調査結果の反映用）。
    corp_number → ticker の順で一致行を探し、見つかれば行ごと record で置き換えて
    True を返す。見つからなければ何もせず False を返す（追記はしない。add を使う）。"""
    records = read_registry()
    cn = record.get("corp_number")
    tk = normalize_ticker(record.get("ticker"))
    idx = None
    for i, r in enumerate(records):
        if cn and r.get("corp_number") and str(r["corp_number"]) == str(cn):
            idx = i
            break
    if idx is None and tk:
        for i, r in enumerate(records):
            if normalize_ticker(r.get("ticker")) == tk:
                idx = i
                break
    if idx is None:
        return False
    records[idx] = record
    path = registry_path()
    ensure_dir(os.path.dirname(path))
    with open(path, "w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    return True


# ---------------------------------------------------------------------------
# 健全性コア条件の決定論的判定
# ---------------------------------------------------------------------------

def _is_increasing(history, allow_cuts=0, tolerance=0.0):
    """時系列(古い→新しい)が右肩上がりか（配当/売上/EPS 共通）。
    連続する期で「後 < 前 - tolerance」を減少としてカウントし、allow_cuts 以下なら True。
    維持(同額)は減少にしない。記念配など外れ値の除外は呼び出し側の前処理に委ねる。"""
    vals = [v for v in history if isinstance(v, (int, float))]
    if len(vals) < 2:
        return None, 0  # 判定不能
    cuts = 0
    for prev, cur in zip(vals, vals[1:]):
        if cur < prev - tolerance:
            cuts += 1
    return (cuts <= allow_cuts), cuts


def _recent_window(history, periods):
    """時系列(古い→新しい)から直近 periods 期の数値ウィンドウを切り出す。
    戻り値 (window, numeric): window は直近 periods 件（そのまま）、
    numeric はそのうち数値だけ。len(numeric) < periods ならデータ不足（欠損 null 含む）。"""
    hist = history or []
    window = hist[-periods:]
    numeric = [v for v in window if isinstance(v, (int, float))]
    return window, numeric


def _check_increasing(checks, insufficient, key, history, periods, allow_declines, label):
    """「直近 periods 期で減少年 allow_declines 回以下」の共通チェック。checks に結果を格納。"""
    window, numeric = _recent_window(history, periods)
    if len(numeric) < periods:
        checks[key] = {
            "ok": False, "value": window,
            "detail": "%sが %d 期未満(欠損含む)" % (label, periods),
        }
        insufficient.append(key)
        return
    inc, declines = _is_increasing(numeric, allow_cuts=allow_declines)
    checks[key] = {
        "ok": bool(inc), "value": window,
        "detail": "直近%d期 減少 %d 回 (許容 %d 回)" % (periods, declines, allow_declines),
    }


def _check_cagr(checks, insufficient, key, history, periods, min_cagr, label):
    """「直近 periods 期の年平均成長率(CAGR) ≧ min_cagr %」の共通チェック。checks に結果を格納。
    CAGR = (末値/初値)^(1/(期数-1)) - 1。初値が 0 以下（無配スタート・赤字スタート等）は
    幾何成長率を定義できないため判定不能（insufficient = 要再確認で保留）とする。"""
    window, numeric = _recent_window(history, periods)
    if len(numeric) < periods:
        checks[key] = {
            "ok": False, "value": window,
            "detail": "%sが %d 期未満(欠損含む)" % (label, periods),
        }
        insufficient.append(key)
        return
    first, last = numeric[0], numeric[-1]
    if first <= 0:
        checks[key] = {
            "ok": False, "value": window,
            "detail": "%sの初期値が 0 以下で成長率を算出不能(要再確認)" % label,
        }
        insufficient.append(key)
        return
    if last <= 0:
        checks[key] = {
            "ok": False, "value": window,
            "detail": "%sの直近値が 0 以下" % label,
        }
        return
    cagr = ((float(last) / float(first)) ** (1.0 / (periods - 1)) - 1.0) * 100.0
    checks[key] = {
        # 1e-9 は浮動小数点の丸め誤差の吸収（べき根計算でちょうど閾値の値がわずかに下振れするため）
        "ok": cagr >= min_cagr - 1e-9, "value": window,
        "detail": "%s 年平均成長率 %.2f%% (下限 %.2f%%)" % (label, cagr, min_cagr),
    }


def _check_all_positive(checks, insufficient, key, history, periods, label):
    """「直近 periods 期すべて黒字(> 0)」の共通チェック。checks に結果を格納。"""
    window, numeric = _recent_window(history, periods)
    if len(numeric) < periods:
        checks[key] = {
            "ok": False, "value": window,
            "detail": "%sが %d 期未満(欠損含む)" % (label, periods),
        }
        insufficient.append(key)
        return
    all_pos = all(v > 0 for v in numeric)
    n_neg = sum(1 for v in numeric if v <= 0)
    checks[key] = {
        "ok": all_pos, "value": window,
        "detail": "直近%d期 全黒字" % periods if all_pos else "直近%d期中 赤字/ゼロ %d 期" % (periods, n_neg),
    }


def judge_company(data, cfg=None):
    """1 社の取得済み指標から健全性コア条件を決定論的に評価し、判定結果 dict を返す。

    入力 data（取れた値のみ。取れない値は省略 or null。時系列はすべて古い→新しい）:
      {
        "ticker": "1234", "name": "...",
        "yield": 4.3,                       # 配当利回り(%)
        "payout_ratio": 38.0,               # 配当性向(%)
        "equity_ratio": 55.0,               # 自己資本比率(%)
        "roe": 12.0,                        # ROE(%)（最新期）
        "dividend_history": [40, 42, 45, 48, 50],          # 直近 5 期
        "op_profit_history": [90, ..., 160],               # 直近 10 期
        "op_cf_history": [100, ..., 180],                  # 直近 10 期
        "revenue_history": [1000, 1100, 1150, 1200, 1300], # 直近 5 期
        "eps_history": [80, 85, 90, 100, 110],             # 直近 5 期
        "sources": ["url", ...]
      }
    出力:
      { "passed": bool, "checks": {cond: {ok, value, detail}}, "reasons": [...],
        "insufficient": [条件名...] }   # insufficient はデータ不足で判定不能だった条件
    """
    cfg = cfg or {}
    yield_min = float(config_value(cfg, "yield_min", 4.0))
    payout_max = float(config_value(cfg, "payout_max", 50.0))
    dividend_periods = int(config_value(cfg, "dividend_periods", 5))
    allow_cuts = int(config_value(cfg, "allow_dividend_cuts", 0))
    op_profit_periods = int(config_value(cfg, "op_profit_periods", 10))
    op_cf_periods = int(config_value(cfg, "op_cf_periods", 10))
    revenue_periods = int(config_value(cfg, "revenue_periods", 5))
    allow_revenue_declines = int(config_value(cfg, "allow_revenue_declines", 1))
    eps_periods = int(config_value(cfg, "eps_periods", 5))
    allow_eps_declines = int(config_value(cfg, "allow_eps_declines", 1))
    equity_ratio_min = float(config_value(cfg, "equity_ratio_min", 40.0))

    checks = {}
    reasons = []
    insufficient = []

    # 除外区分（REIT 等）。除外なら即不合格。
    exclude_patterns = cfg.get("exclude_name_patterns")
    ex = exclusion_reason(data.get("name"), exclude_patterns)
    checks["not_excluded_type"] = {
        "ok": ex is None,
        "value": data.get("name"),
        "detail": "除外区分に該当(%s)" % ex if ex else "REIT/投資法人/インフラF 等に非該当",
    }

    # 条件1: 利回り >= 閾値
    y = data.get("yield")
    if isinstance(y, (int, float)):
        ok = y >= yield_min
        checks["yield"] = {"ok": ok, "value": y, "detail": "利回り %.2f%% (閾値 %.2f%%)" % (y, yield_min)}
    else:
        checks["yield"] = {"ok": False, "value": None, "detail": "利回り未取得"}
        insufficient.append("yield")

    # 条件2: 配当が右肩上がり（直近 dividend_periods 期で減配 allow_cuts 回以下。同額維持は OK）
    _check_increasing(checks, insufficient, "dividend_increasing",
                      data.get("dividend_history"), dividend_periods, allow_cuts, "配当推移")

    # 条件3: 配当性向 < 上限
    pr = data.get("payout_ratio")
    if isinstance(pr, (int, float)):
        ok = pr < payout_max
        checks["payout_ratio"] = {"ok": ok, "value": pr, "detail": "配当性向 %.1f%% (上限 %.1f%%)" % (pr, payout_max)}
    else:
        checks["payout_ratio"] = {"ok": False, "value": None, "detail": "配当性向未取得"}
        insufficient.append("payout_ratio")

    # 条件4: 営業利益に赤字なし（直近 op_profit_periods 期すべて黒字）
    _check_all_positive(checks, insufficient, "op_profit_positive",
                        data.get("op_profit_history"), op_profit_periods, "営業利益")

    # 条件5: 売上高が右肩上がり（直近 revenue_periods 期で減少 allow_revenue_declines 回以下）
    _check_increasing(checks, insufficient, "revenue_increasing",
                      data.get("revenue_history"), revenue_periods, allow_revenue_declines, "売上推移")

    # 条件6: 自己資本比率 >= 下限
    eq = data.get("equity_ratio")
    if isinstance(eq, (int, float)):
        ok = eq >= equity_ratio_min
        checks["equity_ratio"] = {
            "ok": ok, "value": eq,
            "detail": "自己資本比率 %.1f%% (下限 %.1f%%)" % (eq, equity_ratio_min),
        }
    else:
        checks["equity_ratio"] = {"ok": False, "value": None, "detail": "自己資本比率未取得"}
        insufficient.append("equity_ratio")

    # 条件7: EPS が右肩上がり（直近 eps_periods 期で減少 allow_eps_declines 回以下）
    _check_increasing(checks, insufficient, "eps_increasing",
                      data.get("eps_history"), eps_periods, allow_eps_declines, "EPS推移")

    # 条件8: 営業CF に赤字なし（直近 op_cf_periods 期すべて黒字）
    _check_all_positive(checks, insufficient, "op_cf_positive",
                        data.get("op_cf_history"), op_cf_periods, "営業CF")

    # 条件9: 増配率（直近 dividend_periods 期の年平均成長率 ≧ dividend_cagr_min %）
    dividend_cagr_min = float(config_value(cfg, "dividend_cagr_min", 5.0))
    _check_cagr(checks, insufficient, "dividend_cagr",
                data.get("dividend_history"), dividend_periods, dividend_cagr_min, "配当")

    # 条件10: EPS 成長率（直近 eps_periods 期の年平均成長率 ≧ eps_cagr_min %）
    eps_cagr_min = float(config_value(cfg, "eps_cagr_min", 5.0))
    _check_cagr(checks, insufficient, "eps_cagr",
                data.get("eps_history"), eps_periods, eps_cagr_min, "EPS")

    # 条件11: ROE >= 下限（最新期）
    roe_min = float(config_value(cfg, "roe_min", 8.0))
    roe = data.get("roe")
    if isinstance(roe, (int, float)):
        checks["roe"] = {
            "ok": roe >= roe_min, "value": roe,
            "detail": "ROE %.1f%% (下限 %.1f%%)" % (roe, roe_min),
        }
    else:
        checks["roe"] = {"ok": False, "value": None, "detail": "ROE 未取得"}
        insufficient.append("roe")

    passed = all(c["ok"] for c in checks.values())
    for name, c in checks.items():
        if not c["ok"]:
            reasons.append("%s: %s" % (name, c["detail"]))

    return {
        "ticker": data.get("ticker"),
        "name": data.get("name"),
        "passed": passed,
        "checks": checks,
        "reasons": reasons,
        "insufficient": insufficient,
    }


if __name__ == "__main__":
    # 簡易セルフテスト
    cfg = load_config()
    print("[config] %s" % (list(cfg.keys()) if cfg else "（未設定 / 既定値で動作）"))
    print("[data_home] %s" % data_home())
    print("[normalize] 72030 -> %s / 7203 -> %s / 130A0 -> %s" % (
        normalize_ticker("72030"), normalize_ticker("7203"), normalize_ticker("130A0")))
    demo = {
        "ticker": "9999", "name": "テスト株式会社", "yield": 4.5, "payout_ratio": 38.0,
        "equity_ratio": 55.0, "roe": 12.0,
        "dividend_history": [40, 42, 45, 48, 50],
        "op_profit_history": [90, 100, 110, 115, 120, 130, 140, 150, 155, 160],
        "op_cf_history": [100, 110, 120, 125, 130, 140, 150, 160, 170, 180],
        "revenue_history": [1000, 1100, 1150, 1200, 1300],
        "eps_history": [80, 85, 90, 100, 110],
    }
    res = judge_company(demo, cfg)
    print("[judge] passed=%s reasons=%s" % (res["passed"], res["reasons"]))
    reit = dict(demo, name="○○リート投資法人")
    print("[judge:REIT] passed=%s" % judge_company(reit, cfg)["passed"])
    # 新条件の境界ケース
    dip1 = dict(demo, revenue_history=[1000, 1100, 1050, 1200, 1300])  # 減少1回 → 許容内で合格
    dip2 = dict(demo, eps_history=[80, 75, 70, 100, 110])              # 減少2回 → 不合格
    low_eq = dict(demo, equity_ratio=35.0)                             # 自己資本比率 40% 未満 → 不合格
    short_op = dict(demo, op_profit_history=[120, 130, 150, 160])     # 10期未満 → insufficient
    cf_neg = dict(demo, op_cf_history=[100, -5, 120, 125, 130, 140, 150, 160, 170, 180])  # 赤字あり → 不合格
    flat_div = dict(demo, dividend_history=[50, 50, 50, 50, 50])           # 増配率 0% → 不合格
    zero_start = dict(demo, dividend_history=[0, 5, 8, 10, 12])            # 無配スタート → insufficient
    low_roe = dict(demo, roe=6.0)                                          # ROE 8% 未満 → 不合格
    print("[judge:rev-dip1] passed=%s" % judge_company(dip1, cfg)["passed"])
    print("[judge:flat-div] passed=%s reasons=%s" % (
        judge_company(flat_div, cfg)["passed"],
        [r for r in judge_company(flat_div, cfg)["reasons"] if "dividend_cagr" in r]))
    rz = judge_company(zero_start, cfg)
    print("[judge:zero-start-div] passed=%s insufficient=%s" % (rz["passed"], rz["insufficient"]))
    print("[judge:low-roe] passed=%s" % judge_company(low_roe, cfg)["passed"])
    print("[judge:eps-dip2] passed=%s reasons=%s" % (
        judge_company(dip2, cfg)["passed"], judge_company(dip2, cfg)["reasons"]))
    print("[judge:low-equity] passed=%s" % judge_company(low_eq, cfg)["passed"])
    r = judge_company(short_op, cfg)
    print("[judge:short-op] passed=%s insufficient=%s" % (r["passed"], r["insufficient"]))
    print("[judge:cf-neg] passed=%s" % judge_company(cf_neg, cfg)["passed"])
    pats = cfg.get("exclude_name_patterns")
    print("[exclude] 日本コンクリート工業 -> %s / ジャパンリート -> %s" % (
        exclusion_reason("日本コンクリート工業", pats), exclusion_reason("ジャパンリート", pats)))
