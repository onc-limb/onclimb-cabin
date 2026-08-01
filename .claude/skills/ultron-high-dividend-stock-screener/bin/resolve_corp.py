#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""証券コード → 法人番号(13桁) を EDINET コードリストで解決する。

本筋: EDINET の「EDINET コードリスト」(EdinetcodeDlInfo.csv) の
      「証券コード」「提出者法人番号」カラムを突合して法人番号を得る。
      上場企業は有報提出義務があるためほぼ必ず載る。一度 DL すればローカル突合で済む。

フォールバック: ここで引けない例外は SKILL.md の手順に従い国税庁 法人番号 Web-API で
      社名照合する（曖昧なため人手確認に回す）。本スクリプトは EDINET 突合のみ担当。

使い方:
  python3 resolve_corp.py 7203 8058 9433        # 証券コードを引数で
  echo "7203\n8058" | python3 resolve_corp.py --stdin
  python3 resolve_corp.py --refresh 7203         # コードリストを再取得してから引く
出力: JSON 配列 [{"ticker","corp_number","name","listing","found"}...]
"""
import io
import json
import os
import sys
import zipfile
from urllib.request import Request, urlopen

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hdss_lib as L  # noqa: E402

# EDINET コードリスト（ZIP, 中に EdinetcodeDlInfo.csv / Shift-JIS）
EDINET_ZIP_URL = "https://disclosure2dl.edinet-fsa.go.jp/searchdocument/codelist/Edinetcode.zip"
CSV_NAME = "EdinetcodeDlInfo.csv"


def _csv_cache_path():
    return os.path.join(L.edinet_dir(), CSV_NAME)


def download_codelist():
    """EDINET コードリスト ZIP を取得し、中の CSV をキャッシュへ展開する。"""
    L.ensure_dir(L.edinet_dir())
    req = Request(EDINET_ZIP_URL, headers={"User-Agent": "Mozilla/5.0 (hdss-skill)"})
    with urlopen(req, timeout=60) as resp:
        blob = resp.read()
    with zipfile.ZipFile(io.BytesIO(blob)) as zf:
        # 大文字小文字や階層の揺れに備えて末尾一致で探す
        target = None
        for n in zf.namelist():
            if n.lower().endswith(CSV_NAME.lower()):
                target = n
                break
        if target is None:
            raise RuntimeError("ZIP 内に %s が見つかりません: %s" % (CSV_NAME, zf.namelist()))
        data = zf.read(target)
    path = _csv_cache_path()
    with open(path, "wb") as f:
        f.write(data)
    return path


def _read_csv_rows(path):
    """EDINET CSV(Shift-JIS, ダブルクォート)を行→セル list で返す。"""
    import csv
    # cp932 で読む。先頭にダウンロード情報行・ヘッダ行が入る形式。
    with open(path, "r", encoding="cp932", errors="replace", newline="") as f:
        return list(csv.reader(f))


def build_index(path):
    """CSV から {正規化証券コード: {corp_number, name, listing}} を構築。"""
    rows = _read_csv_rows(path)
    # ヘッダ行を「証券コード」「提出者法人番号」を含む行として検出
    header_idx = None
    for i, row in enumerate(rows[:5]):
        joined = ",".join(row)
        if "証券コード" in joined and "提出者法人番号" in joined:
            header_idx = i
            break
    if header_idx is None:
        raise RuntimeError("CSV にヘッダ(証券コード/提出者法人番号)が見つかりません")
    header = rows[header_idx]

    def col(*names):
        for j, h in enumerate(header):
            hs = (h or "").strip()
            if hs in names:
                return j
        return None

    ci_code = col("証券コード")
    ci_corp = col("提出者法人番号")
    ci_name = col("提出者名")
    ci_listing = col("上場区分")
    if ci_code is None or ci_corp is None:
        raise RuntimeError("必要カラムの列位置を特定できません: %s" % header)

    index = {}
    for row in rows[header_idx + 1:]:
        if len(row) <= max(ci_code, ci_corp):
            continue
        raw_code = (row[ci_code] or "").strip()
        if not raw_code:
            continue
        norm = L.normalize_ticker(raw_code)
        if not norm:
            continue
        rec = {
            "corp_number": (row[ci_corp] or "").strip() or None,
            "name": (row[ci_name].strip() if ci_name is not None and len(row) > ci_name else None),
            "listing": (row[ci_listing].strip() if ci_listing is not None and len(row) > ci_listing else None),
        }
        # 正規化キーと生キーの両方で引けるようにする
        index.setdefault(norm, rec)
        index.setdefault(raw_code, rec)
    return index


def resolve(tickers, refresh=False):
    path = _csv_cache_path()
    if refresh or not os.path.exists(path):
        try:
            path = download_codelist()
        except Exception as e:
            return None, "EDINET コードリストの取得に失敗: %s" % e
    try:
        index = build_index(path)
    except Exception as e:
        return None, "コードリストの解析に失敗: %s" % e

    out = []
    for t in tickers:
        norm = L.normalize_ticker(t)
        rec = index.get(norm) or (index.get(str(t).strip()) if t else None)
        if rec:
            out.append({
                "ticker": norm,
                "corp_number": rec.get("corp_number"),
                "name": rec.get("name"),
                "listing": rec.get("listing"),
                "found": bool(rec.get("corp_number")),
            })
        else:
            out.append({"ticker": norm, "corp_number": None, "name": None,
                        "listing": None, "found": False})
    return out, None


def main(argv):
    refresh = False
    args = []
    use_stdin = False
    for a in argv:
        if a == "--refresh":
            refresh = True
        elif a == "--stdin":
            use_stdin = True
        else:
            args.append(a)
    if use_stdin:
        args += [ln.strip() for ln in sys.stdin.read().splitlines() if ln.strip()]
    if not args:
        sys.stderr.write("使い方: resolve_corp.py [--refresh] [--stdin] <証券コード...>\n")
        return 2
    out, err = resolve(args, refresh=refresh)
    if err:
        print(json.dumps({"error": err}, ensure_ascii=False))
        return 1
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
