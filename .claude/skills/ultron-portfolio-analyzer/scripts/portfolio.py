#!/usr/bin/env python3
"""portfolio.py — SBI 証券ポートフォリオの決定論的な取り込み・分析ツール（stdlib only）。

リポジトリ直下 portfolio-data/（git 管理外）を、スキーマ検証・検算付きで安全に操作する。
Claude は損益・利回りの合算を暗算せず、必ず本スクリプトの出力を転記する。

データ配置（--data-dir > $PORTFOLIO_DATA > <repo>/portfolio-data）:
  inbox/                     取り込み待ちの生 CSV（保有一覧 / 配当履歴）
  snapshots/holdings-<date>.json  正規化した保有スナップショット（時系列で積む）
  dividends.json             受取配当・分配金の累積台帳（重複排除）
  sector-map.json            銘柄コード→業種のマッピング台帳（積み増し）
  reports/analysis-<date>.md 生成した分析レポート

コマンド:
  import-holdings  SBI 保有一覧 CSV を正規化して snapshot に保存
  import-dividends SBI 配当・分配金履歴 CSV を dividends.json へ累積
  sector           マッピング台帳の欠損確認・追記・一覧（missing / set / list）
  analyze          最新（or 指定日）snapshot を分析してレポート出力
  list-snapshots   保存済み snapshot の一覧

数値・損益はすべて本スクリプトが計算する。取得額(cost) = 評価額 - 損益 を全区分共通の
真値とし、個別株は取得単価×数量で検算する。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import OrderedDict, defaultdict
from datetime import datetime, timezone
from pathlib import Path

# 既定のデータディレクトリ: <repo>/portfolio-data（git 管理外）
# __file__ = <repo>/.claude/skills/ultron-portfolio-analyzer/scripts/portfolio.py
REPO_ROOT = Path(__file__).resolve().parents[4]
DEFAULT_DATA_DIR = REPO_ROOT / "portfolio-data"

DISCLAIMER = (
    "本レポートは証券口座の保有情報の機械的な整理・集計であり、投資助言ではありません。"
    "数値は取り込み元 CSV との照合を推奨します。セクター分類・配当は登録済み台帳に依存し、"
    "未登録・未取り込み分は集計に含まれません。"
)

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# 東証の 33 業種分類（sector-map.json の sector 値の統制に使う。詳細は references/sector-taxonomy.md）
TSE_33_SECTORS = [
    "水産・農林業", "鉱業", "建設業", "食料品", "繊維製品", "パルプ・紙", "化学", "医薬品",
    "石油・石炭製品", "ゴム製品", "ガラス・土石製品", "鉄鋼", "非鉄金属", "金属製品", "機械",
    "電気機器", "輸送用機器", "精密機器", "その他製品", "電気・ガス業", "陸運業", "海運業",
    "空運業", "倉庫・運輸関連業", "情報・通信業", "卸売業", "小売業", "銀行業", "証券・商品先物取引業",
    "保険業", "その他金融業", "不動産業", "サービス業",
]


# ---------------------------------------------------------------------------
# 基盤ユーティリティ
# ---------------------------------------------------------------------------

def resolve_data_dir(cli_value: str | None) -> Path:
    if cli_value:
        return Path(cli_value).expanduser()
    env = os.environ.get("PORTFOLIO_DATA")
    if env:
        return Path(env).expanduser()
    return DEFAULT_DATA_DIR


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def read_text_any_encoding(path: Path) -> str:
    """SBI の CSV は Shift_JIS(CP932) が既定。UTF-8(BOM 含む) も許容する。"""
    raw = path.read_bytes()
    for enc in ("utf-8-sig", "cp932", "shift_jis", "utf-8"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    # 最後の手段: 置換しつつ CP932 で読む（欠損を避ける）
    return raw.decode("cp932", errors="replace")


def parse_num(s: str):
    """"+1,234.5" / "-32.19" / "8274" / "" → float|None。カンマ・符号・空白を許容。"""
    if s is None:
        return None
    t = str(s).strip().replace(",", "").replace("　", "")
    if t in ("", "-", "--", "----", "*", "―"):
        return None
    t = t.lstrip("+")
    try:
        return float(t)
    except ValueError:
        return None


def as_int_if_whole(x):
    if x is None:
        return None
    if abs(x - round(x)) < 1e-9:
        return int(round(x))
    return x


def split_csv_line(line: str) -> list[str]:
    """1 行を CSV フィールドに分割（簡易・ダブルクオート対応、SBI の形式に十分）。"""
    fields = []
    cur = []
    in_q = False
    i = 0
    while i < len(line):
        ch = line[i]
        if in_q:
            if ch == '"':
                if i + 1 < len(line) and line[i + 1] == '"':
                    cur.append('"')
                    i += 1
                else:
                    in_q = False
            else:
                cur.append(ch)
        else:
            if ch == '"':
                in_q = True
            elif ch == ",":
                fields.append("".join(cur))
                cur = []
            else:
                cur.append(ch)
        i += 1
    fields.append("".join(cur))
    return [f.strip() for f in fields]


def iter_csv_rows(text: str):
    # SBI の CSV は NEL(U+0085) 等の特殊改行が混ざることがあるため広めに正規化
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("", "\n")
    for line in text.split("\n"):
        if line.strip() == "":
            continue
        yield split_csv_line(line)


def load_json(path: Path, default):
    if not path.exists():
        return default
    raw = path.read_text(encoding="utf-8").strip()
    if not raw:
        return default
    return json.loads(raw)


def save_json(obj, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


# 証券コード: 数字を1つ以上含む 3〜5 文字の半角英数字（"2148" / "545A" / "130A" 等）
CODE_RE = re.compile(r"^(?=.*\d)[0-9A-Za-z]{3,5}$")


def split_code_name(cell: str):
    """"2148 ＩＴＭ"（先頭コード）も "キャリアリンク 6070"（末尾コード）も両対応。

    保有一覧 CSV は「コード 銘柄名」、配当履歴 CSV は「銘柄名 コード」で並びが逆。
    先頭トークン→末尾トークンの順にコード判定する。日本株の銘柄名は空白を含まない前提。
    """
    s = cell.strip().replace("　", " ")
    parts = s.split()
    if len(parts) >= 2:
        if CODE_RE.match(parts[0]):
            return parts[0], " ".join(parts[1:]).strip()
        if CODE_RE.match(parts[-1]):
            return parts[-1], " ".join(parts[:-1]).strip()
    return None, s


# ---------------------------------------------------------------------------
# 保有一覧 CSV のパース
# ---------------------------------------------------------------------------

# 個別株セクションのヘッダは "銘柄（コード）" で始まる。投信は "ファンド名" で始まる。
STOCK_HEADER_KEY = "銘柄"
FUND_HEADER_KEY = "ファンド名"
# セクション見出しの判定に使うキーワード
SECTION_STOCK_KEY = "株式"
SECTION_FUND_KEY = "投資信託"
TOTAL_ROW_KEY = "合計"      # "…合計" 見出し行
GRAND_TOTAL_KEY = "総合計"


def _clean_account_label(raw: str) -> str:
    """"株式（現物/NISA預り（成長投資枠））" → "現物/NISA(成長投資枠)" のように口座区分を短縮。"""
    s = raw.replace("（", "(").replace("）", ")").replace("預り", "")
    # 先頭の資産種別ラベルを外す
    for prefix in ("株式(", "投資信託(金額/", "投資信託("):
        if s.startswith(prefix):
            s = s[len(prefix):]
            if s.endswith(")"):
                s = s[:-1]
            break
    return s.strip("/ ")


def parse_holdings_csv(text: str) -> dict:
    """SBI 保有一覧 CSV を {stocks, funds, reportedTotals} に正規化する。"""
    stocks = []
    funds = []
    reported_totals = []

    mode = None            # "stock" | "fund" | None
    current_account = None
    pending_total_label = None  # 直前に出た "…合計" 見出しの区分名

    rows = list(iter_csv_rows(text))
    for row in rows:
        if not row:
            continue
        first = row[0].strip()

        # --- セクション見出し行の検出（1 セル or 実データが無い見出し） ---
        non_empty = [c for c in row if c.strip() != ""]
        is_heading = len(non_empty) == 1

        if is_heading:
            if GRAND_TOTAL_KEY in first:
                mode = "grandtotal"
                pending_total_label = "総合計"
                continue
            if TOTAL_ROW_KEY in first:
                # "株式(現物/NISA預り(成長投資枠))合計" のような区分合計見出し
                pending_total_label = _clean_account_label(first.replace("合計", ""))
                mode = "total"
                continue
            if SECTION_STOCK_KEY in first and "合計" not in first:
                mode = "stock"
                current_account = _clean_account_label(first)
                continue
            if SECTION_FUND_KEY in first and "合計" not in first:
                mode = "fund"
                current_account = _clean_account_label(first)
                continue
            # その他の 1 セル行（"ポートフォリオ一覧" 等）は無視
            continue

        # --- カラムヘッダ行（"銘柄（コード）,…" / "ファンド名,…"）はスキップ ---
        if first.startswith(STOCK_HEADER_KEY) or first.startswith(FUND_HEADER_KEY):
            continue
        # 合計セクションのカラムヘッダ "評価額,含み損益,…"
        if first in ("評価額",):
            continue

        # --- 合計セクションの数値行 ---
        if mode in ("total", "grandtotal"):
            mv = parse_num(row[0]) if len(row) > 0 else None
            pl = parse_num(row[1]) if len(row) > 1 else None
            plpct = parse_num(row[2]) if len(row) > 2 else None
            if mv is not None:
                reported_totals.append({
                    "section": pending_total_label or "?",
                    "marketValue": mv,
                    "profitLoss": pl,
                    "profitLossPct": plpct,
                })
            pending_total_label = None
            continue

        # --- 明細行（個別株 / 投信） ---
        # 列: 名称, 買付日, 数量, 取得単価, 現在値, 前日比, 前日比%, 損益, 損益%, 評価額
        if mode == "stock":
            code, name = split_code_name(row[0])
            rec = _parse_holding_row(row, current_account)
            if rec is None:
                continue
            rec["code"] = code
            rec["name"] = name
            stocks.append(rec)
        elif mode == "fund":
            rec = _parse_holding_row(row, current_account)
            if rec is None:
                continue
            rec["name"] = row[0].strip()
            funds.append(rec)
        # mode None のときの明細は想定外なので無視

    return {"stocks": stocks, "funds": funds, "reportedTotals": reported_totals}


def _parse_holding_row(row: list[str], account: str | None):
    def col(i):
        return row[i] if i < len(row) else ""
    buy_date = col(1).strip()
    if buy_date in ("----/--/--", "--", ""):
        buy_date = None
    else:
        buy_date = buy_date.replace("/", "-")
    quantity = parse_num(col(2))
    acq_price = parse_num(col(3))
    cur_price = parse_num(col(4))
    profit = parse_num(col(7))
    profit_pct = parse_num(col(8))
    market_value = parse_num(col(9))
    if market_value is None:
        return None
    # 取得額(cost) は「評価額 - 損益」を全区分共通の真値とする
    cost = None
    if market_value is not None and profit is not None:
        cost = market_value - profit
    return {
        "account": account,
        "buyDate": buy_date,
        "quantity": as_int_if_whole(quantity),
        "acquisitionPrice": acq_price,
        "currentPrice": cur_price,
        "profitLoss": profit,
        "profitLossPct": profit_pct,
        "marketValue": market_value,
        "cost": cost,
    }


# ---------------------------------------------------------------------------
# 配当履歴 CSV のパース
# ---------------------------------------------------------------------------

# ASSUMPTION: SBI 証券の「配当金・分配金」履歴 CSV のヘッダ名は口座・出力経路で揺れる。
# ここでは列名にキーワードを含むかで動的にマッピングする（実 CSV を渡されたら調整する）。
DIV_COL_PATTERNS = OrderedDict([
    ("payDate", ["入金日", "受渡日", "支払日", "配当基準日", "権利確定日", "基準日", "日付"]),
    ("code", ["銘柄コード", "コード", "ティッカー"]),
    ("name", ["銘柄名", "銘柄", "ファンド名"]),
    ("quantity", ["数量", "株数", "口数", "保有数"]),
    ("perShare", ["単価", "1株", "一株", "1口", "分配金単価", "配当単価"]),
    ("amount", ["受取金額", "税引後", "手取", "入金額", "配当金額", "分配金額", "金額"]),
    ("amountGross", ["税引前", "配当金額(税引前)", "支払金額"]),
    ("tax", ["税額", "源泉", "税金"]),
])


def _match_div_header(headers: list[str]) -> dict:
    """ヘッダ行 → {field: col_index} を返す。最初に一致した列を採用。"""
    mapping = {}
    used = set()
    for field, keys in DIV_COL_PATTERNS.items():
        for idx, h in enumerate(headers):
            if idx in used:
                continue
            hn = h.strip()
            if any(k in hn for k in keys):
                mapping[field] = idx
                used.add(idx)
                break
    return mapping


def parse_dividends_csv(text: str) -> list[dict]:
    rows = list(iter_csv_rows(text))
    # ヘッダ行を探す（"銘柄" と "金額" 系の両方を含む最初の行）
    # 明細ヘッダは「銘柄名/ファンド名」列を含む行に限定する
    # （"商品","受取額(…)" だけのサマリ見出しを明細ヘッダと誤認しないため "商品" は条件から外す）。
    header_idx = None
    for i, row in enumerate(rows):
        joined = "".join(row)
        if any(k in joined for k in ("銘柄", "ファンド")) and \
           any(k in joined for k in ("金額", "配当", "分配", "受取")):
            header_idx = i
            break
    if header_idx is None:
        raise SystemExit(
            "ERROR: 配当履歴 CSV のヘッダ行を特定できませんでした。"
            "ヘッダに銘柄名と金額を含む行が必要です（references/csv-format.md 参照）。"
        )
    headers = rows[header_idx]
    mapping = _match_div_header(headers)
    if "amount" not in mapping and "amountGross" not in mapping:
        raise SystemExit(
            f"ERROR: 配当金額の列を特定できませんでした。検出ヘッダ: {headers}"
        )

    out = []
    for row in rows[header_idx + 1:]:
        if not any(c.strip() for c in row):
            continue
        # 合計・小計行はスキップ
        if any("合計" in c or "小計" in c for c in row):
            continue

        def get(field):
            idx = mapping.get(field)
            return row[idx] if idx is not None and idx < len(row) else ""

        name_cell = get("name").strip()
        code_cell = get("code").strip()
        code, name = (code_cell, name_cell)
        if not code:
            code, name2 = split_code_name(name_cell)
            if code:
                name = name2
        if not name and not code:
            continue

        amount = parse_num(get("amount"))
        gross = parse_num(get("amountGross"))
        if amount is None:
            amount = gross
        if amount is None:
            continue
        pay_date = get("payDate").strip().replace("/", "-")
        if pay_date and not DATE_RE.match(pay_date):
            # "2025-6-5" のようなゼロ埋め無しを補正
            m = re.match(r"^(\d{4})-(\d{1,2})-(\d{1,2})$", pay_date)
            if m:
                pay_date = f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
        out.append({
            "code": code or None,
            "name": name or None,
            "payDate": pay_date or None,
            "quantity": as_int_if_whole(parse_num(get("quantity"))),
            "perShare": parse_num(get("perShare")),
            "amount": amount,
            "amountGross": gross,
            "tax": parse_num(get("tax")),
        })
    return out


def dividend_key(rec: dict) -> str:
    return f"{rec.get('code') or rec.get('name')}|{rec.get('payDate')}|{rec.get('amount')}"


# ---------------------------------------------------------------------------
# コマンド: import-holdings
# ---------------------------------------------------------------------------

def cmd_import_holdings(args):
    data_dir = resolve_data_dir(args.data_dir)
    src = Path(args.file).expanduser()
    if not src.exists():
        sys.exit(f"ERROR: ファイルが見つかりません: {src}")
    text = read_text_any_encoding(src)
    parsed = parse_holdings_csv(text)

    snap_date = args.date or datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d")
    if not DATE_RE.match(snap_date):
        sys.exit(f"ERROR: --date は YYYY-MM-DD 形式で指定してください: {snap_date}")

    snapshot = {
        "snapshotDate": snap_date,
        "source": src.name,
        "importedAt": now_iso(),
        "stocks": parsed["stocks"],
        "funds": parsed["funds"],
        "reportedTotals": parsed["reportedTotals"],
    }

    # 検算: 明細合計 と CSV の合計行を突き合わせる
    checks = _reconcile(parsed)

    out_path = data_dir / "snapshots" / f"holdings-{snap_date}.json"
    if out_path.exists() and not args.force:
        sys.exit(f"ERROR: {out_path.name} は既に存在します。上書きするなら --force を付けてください。")
    save_json(snapshot, out_path)

    print(f"OK: {len(parsed['stocks'])} 銘柄（個別株） / {len(parsed['funds'])} 本（投信）を取り込みました。")
    print(f"    保存先: {out_path}")
    print()
    print("[検算] 明細の合計 vs CSV 記載の合計行")
    for c in checks:
        mark = "OK" if c["ok"] else "!!"
        print(f"  {mark} {c['label']}: 明細={c['computed']:,} / CSV={c['reported']} 差={c['diff']}")
    missing = _missing_sectors(parsed["stocks"], data_dir)
    if missing:
        print()
        print(f"[注意] セクター未登録の銘柄が {len(missing)} 件あります。"
              f"`sector missing` で確認し `sector set` で登録してください。")


def _reconcile(parsed: dict) -> list[dict]:
    checks = []
    reported = {r["section"]: r for r in parsed["reportedTotals"]}
    # 個別株: account ごとに集計（大半は同一 account）
    by_acc = defaultdict(float)
    for s in parsed["stocks"]:
        if s.get("marketValue") is not None:
            by_acc[s["account"]] += s["marketValue"]
    for acc, mv in by_acc.items():
        rep = _find_reported(reported, acc)
        checks.append(_mk_check(f"株式 {acc}", mv, rep))
    # 投信: account ごと
    by_acc_f = defaultdict(float)
    for f in parsed["funds"]:
        if f.get("marketValue") is not None:
            by_acc_f[f["account"]] += f["marketValue"]
    for acc, mv in by_acc_f.items():
        rep = _find_reported(reported, acc)
        checks.append(_mk_check(f"投信 {acc}", mv, rep))
    return checks


def _find_reported(reported: dict, account: str | None):
    if account is None:
        return None
    # 完全一致を優先（"NISA(成長投資枠)" が "現物/NISA(成長投資枠)" に誤マッチするのを防ぐ）
    if account in reported:
        return reported[account]
    for sec, r in reported.items():
        if account and (account in sec or sec in account):
            return r
    return None


def _mk_check(label, computed, rep):
    reported_mv = rep["marketValue"] if rep else None
    diff = None
    ok = False
    if reported_mv is not None:
        diff = round(computed - reported_mv, 2)
        ok = abs(diff) < 1.0
    return {
        "label": label,
        "computed": round(computed, 2),
        "reported": reported_mv if reported_mv is not None else "（対応なし）",
        "diff": diff if diff is not None else "-",
        "ok": ok,
    }


# ---------------------------------------------------------------------------
# コマンド: import-dividends
# ---------------------------------------------------------------------------

def cmd_import_dividends(args):
    data_dir = resolve_data_dir(args.data_dir)
    src = Path(args.file).expanduser()
    if not src.exists():
        sys.exit(f"ERROR: ファイルが見つかりません: {src}")
    text = read_text_any_encoding(src)
    new_records = parse_dividends_csv(text)

    path = data_dir / "dividends.json"
    existing = load_json(path, [])
    seen = {dividend_key(r) for r in existing}
    added = 0
    dups = 0
    for r in new_records:
        r["source"] = src.name
        r["importedAt"] = now_iso()
        k = dividend_key(r)
        if k in seen:
            dups += 1
            continue
        seen.add(k)
        existing.append(r)
        added += 1

    if args.dry_run:
        print(f"[dry-run] 取り込み対象 {len(new_records)} 件 / 新規 {added} 件 / 重複 {dups} 件")
        for r in new_records[:20]:
            print(f"  {r.get('payDate')} {r.get('code') or ''} {r.get('name') or ''} "
                  f"{r.get('amount'):,.0f}円")
        return

    save_json(existing, path)
    total = sum(r.get("amount") or 0 for r in existing)
    print(f"OK: 配当 {added} 件を追加（重複 {dups} 件スキップ）。台帳合計 {len(existing)} 件 / "
          f"累計受取 {total:,.0f}円")
    print(f"    保存先: {path}")


# ---------------------------------------------------------------------------
# コマンド: sector（missing / set / list）
# ---------------------------------------------------------------------------

def _load_sector_map(data_dir: Path) -> dict:
    return load_json(data_dir / "sector-map.json", {})


def _missing_sectors(stocks: list[dict], data_dir: Path) -> list[dict]:
    smap = _load_sector_map(data_dir)
    missing = []
    seen = set()
    for s in stocks:
        code = s.get("code")
        if not code or code in seen:
            continue
        seen.add(code)
        if code not in smap:
            missing.append({"code": code, "name": s.get("name")})
    return missing


def cmd_sector(args):
    data_dir = resolve_data_dir(args.data_dir)
    path = data_dir / "sector-map.json"
    smap = _load_sector_map(data_dir)

    if args.sector_cmd == "list":
        if not smap:
            print("（sector-map.json は空です）")
            return
        for code in sorted(smap):
            e = smap[code]
            print(f"  {code}  {e.get('sector','?'):<12} {e.get('name','')}")
        print(f"\n登録 {len(smap)} 件")
        return

    if args.sector_cmd == "set":
        if args.sector not in TSE_33_SECTORS:
            print(f"[警告] '{args.sector}' は東証33業種の一覧に無い値です。"
                  f"references/sector-taxonomy.md を確認してください（登録は続行）。")
        smap[args.code] = {
            "name": args.name,
            "sector": args.sector,
            "sector17": args.sector17,
            "updatedAt": now_iso(),
        }
        save_json(smap, path)
        print(f"OK: {args.code} → {args.sector}（{args.name}）を登録しました。")
        return

    if args.sector_cmd == "missing":
        snap = _load_snapshot(data_dir, args.date)
        if snap is None:
            sys.exit("ERROR: snapshot がありません。先に import-holdings を実行してください。")
        missing = _missing_sectors(snap["stocks"], data_dir)
        if not missing:
            print("セクター未登録の銘柄はありません。")
            return
        print(f"セクター未登録の銘柄 {len(missing)} 件（sector set で登録してください）:")
        for m in missing:
            print(f"  {m['code']}  {m['name']}")
        return


# ---------------------------------------------------------------------------
# コマンド: analyze
# ---------------------------------------------------------------------------

def _list_snapshots(data_dir: Path) -> list[Path]:
    d = data_dir / "snapshots"
    if not d.exists():
        return []
    return sorted(d.glob("holdings-*.json"))


def _load_snapshot(data_dir: Path, date: str | None):
    snaps = _list_snapshots(data_dir)
    if not snaps:
        return None
    if date:
        target = data_dir / "snapshots" / f"holdings-{date}.json"
        if not target.exists():
            return None
        return load_json(target, None)
    return load_json(snaps[-1], None)


def _prev_snapshot(data_dir: Path, current_date: str):
    snaps = _list_snapshots(data_dir)
    dates = [p.stem.replace("holdings-", "") for p in snaps]
    prevs = [d for d in dates if d < current_date]
    if not prevs:
        return None
    return load_json(data_dir / "snapshots" / f"holdings-{max(prevs)}.json", None)


def _sum(records, key):
    return sum(r[key] for r in records if r.get(key) is not None)


def analyze_snapshot(snap: dict, data_dir: Path) -> dict:
    smap = _load_sector_map(data_dir)
    dividends = load_json(data_dir / "dividends.json", [])
    stocks = snap["stocks"]
    funds = snap["funds"]

    # --- 全体・資産クラス別 損益 ---
    def bucket(records):
        cost = _sum(records, "cost")
        mv = _sum(records, "marketValue")
        pl = mv - cost
        return {
            "cost": round(cost, 2),
            "marketValue": round(mv, 2),
            "profitLoss": round(pl, 2),
            "profitLossPct": round(pl / cost * 100, 2) if cost else None,
            "count": len(records),
        }

    stock_b = bucket(stocks)
    fund_b = bucket(funds)
    total_b = bucket(stocks + funds)

    # --- 配当（受取実績） ---
    div_total = sum(r.get("amount") or 0 for r in dividends)
    # 個別株の取得額に対する受取配当利回り
    div_yield_on_cost = (div_total / stock_b["cost"] * 100) if stock_b["cost"] else None
    total_return = round(total_b["profitLoss"] + div_total, 2)
    total_return_pct = (total_return / total_b["cost"] * 100) if total_b["cost"] else None

    # --- セクター別（個別株のみ） ---
    sector_agg = defaultdict(lambda: {"cost": 0.0, "marketValue": 0.0, "count": 0, "codes": []})
    unmapped = []
    for s in stocks:
        code = s.get("code")
        sector = smap.get(code, {}).get("sector") if code else None
        if not sector:
            sector = "（未分類）"
            if code:
                unmapped.append({"code": code, "name": s.get("name")})
        a = sector_agg[sector]
        a["cost"] += s.get("cost") or 0
        a["marketValue"] += s.get("marketValue") or 0
        a["count"] += 1
        a["codes"].append(code)
    sectors = []
    stock_mv_total = stock_b["marketValue"] or 1
    for name, a in sector_agg.items():
        pl = a["marketValue"] - a["cost"]
        sectors.append({
            "sector": name,
            "cost": round(a["cost"], 2),
            "marketValue": round(a["marketValue"], 2),
            "profitLoss": round(pl, 2),
            "profitLossPct": round(pl / a["cost"] * 100, 2) if a["cost"] else None,
            "weightPct": round(a["marketValue"] / stock_mv_total * 100, 2),
            "count": a["count"],
        })
    sectors.sort(key=lambda x: x["marketValue"], reverse=True)

    # --- 口座区分別（株＋投信） ---
    acct_agg = defaultdict(lambda: {"cost": 0.0, "marketValue": 0.0, "count": 0})
    for r in stocks + funds:
        a = acct_agg[r.get("account") or "（不明）"]
        a["cost"] += r.get("cost") or 0
        a["marketValue"] += r.get("marketValue") or 0
        a["count"] += 1
    accounts = []
    for name, a in acct_agg.items():
        pl = a["marketValue"] - a["cost"]
        accounts.append({
            "account": name,
            "cost": round(a["cost"], 2),
            "marketValue": round(a["marketValue"], 2),
            "profitLoss": round(pl, 2),
            "profitLossPct": round(pl / a["cost"] * 100, 2) if a["cost"] else None,
            "count": a["count"],
        })
    accounts.sort(key=lambda x: x["marketValue"], reverse=True)

    # --- 集中度（個別株） ---
    stock_sorted = sorted(
        [s for s in stocks if s.get("marketValue")],
        key=lambda s: s["marketValue"], reverse=True,
    )
    top5_share = None
    max_share = None
    hhi = None
    if stock_mv_total:
        top5 = stock_sorted[:5]
        top5_share = round(sum(s["marketValue"] for s in top5) / stock_mv_total * 100, 2)
        if stock_sorted:
            max_share = round(stock_sorted[0]["marketValue"] / stock_mv_total * 100, 2)
        hhi = round(sum((s["marketValue"] / stock_mv_total * 100) ** 2 for s in stock_sorted), 1)

    # --- 勝ち負けランキング（個別株、損益率） ---
    ranked = sorted(
        [s for s in stocks if s.get("profitLossPct") is not None],
        key=lambda s: s["profitLossPct"], reverse=True,
    )

    def slim(s):
        return {
            "code": s.get("code"), "name": s.get("name"),
            "profitLoss": s.get("profitLoss"), "profitLossPct": s.get("profitLossPct"),
            "marketValue": s.get("marketValue"),
        }

    winners = [slim(s) for s in ranked[:5]]
    losers = [slim(s) for s in ranked[-5:]][::-1] if len(ranked) >= 1 else []

    return {
        "snapshotDate": snap["snapshotDate"],
        "source": snap.get("source"),
        "overall": {
            "stock": stock_b, "fund": fund_b, "total": total_b,
        },
        "dividends": {
            "totalReceived": round(div_total, 2),
            "count": len(dividends),
            "yieldOnStockCostPct": round(div_yield_on_cost, 2) if div_yield_on_cost is not None else None,
            "totalReturnInclDividend": total_return,
            "totalReturnPct": round(total_return_pct, 2) if total_return_pct is not None else None,
        },
        "sectors": sectors,
        "unmappedSectors": unmapped,
        "accounts": accounts,
        "concentration": {
            "stockCount": len(stock_sorted),
            "top5SharePct": top5_share,
            "maxSharePct": max_share,
            "hhi": hhi,
            "topHoldings": [slim(s) for s in stock_sorted[:5]],
        },
        "winners": winners,
        "losers": losers,
    }


def _diff_vs_prev(cur_snap, prev_snap, cur_result, data_dir):
    if prev_snap is None:
        return None
    prev_result = analyze_snapshot(prev_snap, data_dir)
    cur_codes = {s["code"]: s for s in cur_snap["stocks"] if s.get("code")}
    prev_codes = {s["code"]: s for s in prev_snap["stocks"] if s.get("code")}
    new_codes = [cur_codes[c] for c in cur_codes if c not in prev_codes]
    sold_codes = [prev_codes[c] for c in prev_codes if c not in cur_codes]
    return {
        "prevDate": prev_snap["snapshotDate"],
        "marketValueDelta": round(
            cur_result["overall"]["total"]["marketValue"]
            - prev_result["overall"]["total"]["marketValue"], 2),
        "profitLossDelta": round(
            cur_result["overall"]["total"]["profitLoss"]
            - prev_result["overall"]["total"]["profitLoss"], 2),
        "newHoldings": [{"code": s.get("code"), "name": s.get("name")} for s in new_codes],
        "soldHoldings": [{"code": s.get("code"), "name": s.get("name")} for s in sold_codes],
    }


def cmd_analyze(args):
    data_dir = resolve_data_dir(args.data_dir)
    snap = _load_snapshot(data_dir, args.date)
    if snap is None:
        sys.exit("ERROR: snapshot がありません。先に import-holdings を実行してください。")
    result = analyze_snapshot(snap, data_dir)
    prev = _prev_snapshot(data_dir, snap["snapshotDate"])
    result["diffVsPrev"] = _diff_vs_prev(snap, prev, result, data_dir)

    if args.json:
        out = json.dumps(result, ensure_ascii=False, indent=2)
    else:
        out = render_markdown(result)

    if args.out:
        Path(args.out).expanduser().write_text(out + "\n", encoding="utf-8")
        print(f"OK: レポートを書き出しました: {args.out}")
    elif not args.json and args.save:
        rp = data_dir / "reports" / f"analysis-{snap['snapshotDate']}.md"
        rp.parent.mkdir(parents=True, exist_ok=True)
        rp.write_text(out + "\n", encoding="utf-8")
        print(f"OK: レポートを保存しました: {rp}")
        print()
        print(out)
    else:
        print(out)


# ---------------------------------------------------------------------------
# Markdown レンダリング
# ---------------------------------------------------------------------------

def _yen(x):
    if x is None:
        return "-"
    return f"{x:,.0f}円"


def _pct(x):
    if x is None:
        return "-"
    sign = "+" if x > 0 else ""
    return f"{sign}{x:.2f}%"


def _signed_yen(x):
    if x is None:
        return "-"
    sign = "+" if x > 0 else ""
    return f"{sign}{x:,.0f}円"


def _share(x):
    """構成比（シェア）は正値のみなので符号を付けない。"""
    return "-" if x is None else f"{x:.2f}%"


def render_markdown(r: dict) -> str:
    L = []
    L.append(f"# ポートフォリオ分析レポート — {r['snapshotDate']}")
    L.append("")
    L.append(f"*出典: {r.get('source','?')} / 分析日 {r['snapshotDate']}*")
    L.append("")

    o = r["overall"]
    t = o["total"]
    L.append("## 1. 概況（払ったお金に対する損益）")
    L.append("")
    L.append("| 区分 | 取得額 | 評価額 | 含み損益 | 損益率 | 銘柄数 |")
    L.append("|------|-------:|-------:|--------:|-------:|------:|")
    L.append(f"| 個別株 | {_yen(o['stock']['cost'])} | {_yen(o['stock']['marketValue'])} | "
             f"{_signed_yen(o['stock']['profitLoss'])} | {_pct(o['stock']['profitLossPct'])} | {o['stock']['count']} |")
    L.append(f"| 投資信託 | {_yen(o['fund']['cost'])} | {_yen(o['fund']['marketValue'])} | "
             f"{_signed_yen(o['fund']['profitLoss'])} | {_pct(o['fund']['profitLossPct'])} | {o['fund']['count']} |")
    L.append(f"| **合計** | **{_yen(t['cost'])}** | **{_yen(t['marketValue'])}** | "
             f"**{_signed_yen(t['profitLoss'])}** | **{_pct(t['profitLossPct'])}** | {t['count']} |")
    L.append("")

    d = r["dividends"]
    L.append("## 2. 配当を含めた総損益")
    L.append("")
    L.append(f"- 受取配当・分配金 累計: **{_yen(d['totalReceived'])}**（{d['count']} 件）")
    L.append(f"- 取得額ベース配当利回り（個別株）: **{_pct(d['yieldOnStockCostPct'])}**")
    L.append(f"- **配当込み総損益: {_signed_yen(d['totalReturnInclDividend'])}**"
             f"（含み損益 {_signed_yen(t['profitLoss'])} ＋ 受取配当 {_yen(d['totalReceived'])}）")
    L.append(f"- 配当込みトータルリターン率: **{_pct(d['totalReturnPct'])}**")
    if d["count"] == 0:
        L.append("")
        L.append("> 配当履歴が未取り込みです。`import-dividends` で SBI の配当・分配金履歴 CSV を"
                 "取り込むと、この節が受取実績で埋まります。")
    L.append("")

    L.append("## 3. セクター（業種）の傾向 — 個別株")
    L.append("")
    L.append("| 業種 | 評価額 | 構成比 | 取得額 | 含み損益 | 損益率 | 銘柄数 |")
    L.append("|------|-------:|------:|-------:|--------:|-------:|------:|")
    for s in r["sectors"]:
        L.append(f"| {s['sector']} | {_yen(s['marketValue'])} | {s['weightPct']:.1f}% | "
                 f"{_yen(s['cost'])} | {_signed_yen(s['profitLoss'])} | {_pct(s['profitLossPct'])} | {s['count']} |")
    L.append("")
    if r["unmappedSectors"]:
        codes = ", ".join(f"{m['code']}({m['name']})" for m in r["unmappedSectors"])
        L.append(f"> ⚠️ 未分類の銘柄: {codes} — `sector set` で登録すると集計に反映されます。")
        L.append("")

    L.append("## 4. 口座区分別の配分")
    L.append("")
    L.append("| 口座区分 | 評価額 | 取得額 | 含み損益 | 損益率 | 保有数 |")
    L.append("|----------|-------:|-------:|--------:|-------:|------:|")
    for a in r["accounts"]:
        L.append(f"| {a['account']} | {_yen(a['marketValue'])} | {_yen(a['cost'])} | "
                 f"{_signed_yen(a['profitLoss'])} | {_pct(a['profitLossPct'])} | {a['count']} |")
    L.append("")

    c = r["concentration"]
    L.append("## 5. 集中度（個別株）")
    L.append("")
    L.append(f"- 保有銘柄数: {c['stockCount']}")
    L.append(f"- 最大銘柄の比率: {_share(c['maxSharePct'])}")
    L.append(f"- 上位5銘柄の比率: {_share(c['top5SharePct'])}")
    L.append(f"- HHI（集中度指数, 個別株評価額ベース）: {c['hhi']}"
             "（1万に近いほど集中・分散するほど小）")
    L.append("")
    if c["topHoldings"]:
        L.append("上位保有:")
        for s in c["topHoldings"]:
            L.append(f"- {s['code']} {s['name']}: {_yen(s['marketValue'])}")
        L.append("")

    L.append("## 6. 勝ち・負け銘柄（個別株、損益率順）")
    L.append("")
    L.append("**含み益トップ5**")
    L.append("")
    for s in r["winners"]:
        L.append(f"- {s['code']} {s['name']}: {_pct(s['profitLossPct'])}（{_signed_yen(s['profitLoss'])}）")
    L.append("")
    L.append("**含み損ワースト5**")
    L.append("")
    for s in r["losers"]:
        L.append(f"- {s['code']} {s['name']}: {_pct(s['profitLossPct'])}（{_signed_yen(s['profitLoss'])}）")
    L.append("")

    dv = r.get("diffVsPrev")
    if dv:
        L.append("## 7. 前回スナップショットとの比較")
        L.append("")
        L.append(f"- 前回: {dv['prevDate']}")
        L.append(f"- 評価額の変化: {_signed_yen(dv['marketValueDelta'])}")
        L.append(f"- 含み損益の変化: {_signed_yen(dv['profitLossDelta'])}")
        if dv["newHoldings"]:
            L.append(f"- 新規保有: " + ", ".join(f"{h['code']} {h['name']}" for h in dv["newHoldings"]))
        if dv["soldHoldings"]:
            L.append(f"- 消えた銘柄（売却/移管）: " + ", ".join(f"{h['code']} {h['name']}" for h in dv["soldHoldings"]))
        L.append("")

    L.append("---")
    L.append("")
    L.append(f"> {DISCLAIMER}")
    return "\n".join(L)


# ---------------------------------------------------------------------------
# コマンド: list-snapshots
# ---------------------------------------------------------------------------

def cmd_list_snapshots(args):
    data_dir = resolve_data_dir(args.data_dir)
    snaps = _list_snapshots(data_dir)
    if not snaps:
        print("（snapshot はまだありません）")
        return
    for p in snaps:
        snap = load_json(p, {})
        print(f"  {snap.get('snapshotDate','?')}  株 {len(snap.get('stocks',[]))} / "
              f"投信 {len(snap.get('funds',[]))}  ({snap.get('source','?')})")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser():
    p = argparse.ArgumentParser(description="SBI 証券ポートフォリオの取り込み・分析ツール")
    p.add_argument("--data-dir", help="データディレクトリ（既定: $PORTFOLIO_DATA or <repo>/portfolio-data）")
    sub = p.add_subparsers(dest="cmd", required=True)

    ih = sub.add_parser("import-holdings", help="保有一覧 CSV を snapshot に取り込む")
    ih.add_argument("--file", required=True)
    ih.add_argument("--date", help="スナップショット日付 YYYY-MM-DD（既定: 今日）")
    ih.add_argument("--force", action="store_true", help="同日 snapshot を上書き")
    ih.set_defaults(func=cmd_import_holdings)

    idv = sub.add_parser("import-dividends", help="配当・分配金履歴 CSV を台帳へ累積")
    idv.add_argument("--file", required=True)
    idv.add_argument("--dry-run", action="store_true")
    idv.set_defaults(func=cmd_import_dividends)

    sc = sub.add_parser("sector", help="セクター台帳の欠損確認・追記・一覧")
    scsub = sc.add_subparsers(dest="sector_cmd", required=True)
    scm = scsub.add_parser("missing", help="snapshot にある未登録銘柄を列挙")
    scm.add_argument("--date")
    scs = scsub.add_parser("set", help="銘柄コードに業種を登録")
    scs.add_argument("--code", required=True)
    scs.add_argument("--name", required=True)
    scs.add_argument("--sector", required=True, help="東証33業種（sector-taxonomy.md 参照）")
    scs.add_argument("--sector17", default=None, help="任意: 東証17業種")
    scsub.add_parser("list", help="登録済み一覧")
    sc.set_defaults(func=cmd_sector)

    an = sub.add_parser("analyze", help="snapshot を分析してレポート出力")
    an.add_argument("--date", help="対象 snapshot 日付（既定: 最新）")
    an.add_argument("--json", action="store_true", help="JSON で出力")
    an.add_argument("--save", action="store_true", help="reports/ に Markdown を保存")
    an.add_argument("--out", help="レポートの書き出し先ファイル")
    an.set_defaults(func=cmd_analyze)

    ls = sub.add_parser("list-snapshots", help="保存済み snapshot 一覧")
    ls.set_defaults(func=cmd_list_snapshots)

    return p


def main():
    args = build_parser().parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
