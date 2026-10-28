//! サブコマンドの解析と実行。
//!
//! 正本は 2 系統（手入力台帳 records.json / レシート transactions.jsonl）。書き込み系
//! コマンドは正本を書き換えたあと、Grafana 用の SQLite 派生ビューを作り直す
//! （`--no-sqlite` で抑止。失敗は警告扱い）。各 `cmd_*` は `Paths` を受け取るので、
//! テストは一時ディレクトリで完結する。

use crate::ledger::{self, HalfYen, RentPass};
use crate::model::{self, Entry, Kind, ManualRecord, Person, Source};
use crate::paths::Paths;
use crate::{clock, sqlite_export, store};

pub fn run(args: &[String]) -> Result<String, String> {
    let Some((command, rest)) = args.split_first() else {
        return Ok(usage());
    };
    let flags = parse_flags(rest)?;
    let paths = Paths::resolve();
    let export = get(&flags, "no-sqlite").is_none();

    match command.as_str() {
        "add" => cmd_add(&flags, &paths, export),
        "delete" | "rm" => cmd_delete(&flags, &paths, export),
        "list" | "ls" => cmd_list(&flags, &paths),
        "balance" | "status" => cmd_balance(&flags, &paths),
        "pass" => cmd_pass(&flags, &paths, export),
        "receipt" => cmd_receipt(&flags, &paths, export),
        "export" => cmd_export(&paths),
        "help" | "--help" | "-h" => Ok(usage()),
        other => Err(format!("不明なサブコマンド: {other}\n\n{}", usage())),
    }
}

type Flags = Vec<(String, String)>;

// --- コマンド ---------------------------------------------------------------

fn cmd_add(flags: &Flags, paths: &Paths, export: bool) -> Result<String, String> {
    let date = get(flags, "date").map_or_else(clock::today, str::to_string);
    validate_date(&date)?;
    let kind = Kind::parse(require(flags, "kind")?)?;
    let payer = Person::parse(require(flags, "payer")?)?;
    let amount = parse_amount(require(flags, "amount")?)?;

    let transfer_to = match (kind, get(flags, "to")) {
        (Kind::Transfer, Some(raw)) => {
            let to = Person::parse(raw)?;
            if to == payer {
                return Err("振込元と振込先が同じです".to_string());
            }
            Some(to)
        }
        (Kind::Transfer, None) => return Err("振込は --to（振込先）が必須です".to_string()),
        (_, Some(_)) => return Err("--to は振込のときだけ指定できます".to_string()),
        (_, None) => None,
    };
    let category = match (kind, get(flags, "category")) {
        (_, Some(raw)) => model::validate_category(raw)?,
        (Kind::Transfer, None) => "振込".to_string(),
        (_, None) => return Err("--category は必須です".to_string()),
    };
    let item = get(flags, "item")
        .map(str::trim)
        .filter(|s| !s.is_empty())
        .unwrap_or(&category)
        .to_string();
    let memo = get(flags, "memo")
        .map(str::trim)
        .filter(|s| !s.is_empty())
        .map(str::to_string);

    let mut records = store::read_records(&paths.records)?;
    let record = ManualRecord {
        amount,
        category,
        date,
        id: store::next_id(&records),
        item,
        kind,
        memo,
        payer,
        transfer_to,
    };
    records.push(record.clone());
    store::write_records(&paths.records, &records)?;

    let entry = Entry::from_manual(&record);
    Ok(format!(
        "追加しました {}{}",
        entry_line(&entry),
        after_write(paths, export)
    ))
}

fn cmd_delete(flags: &Flags, paths: &Paths, export: bool) -> Result<String, String> {
    let id: i64 = require(flags, "id")?
        .trim()
        .parse()
        .map_err(|_| "id は整数です".to_string())?;
    let mut records = store::read_records(&paths.records)?;
    let position = records
        .iter()
        .position(|r| r.id == id)
        .ok_or_else(|| format!("id={id} のレコードがありません"))?;
    let removed = records.remove(position);
    store::write_records(&paths.records, &records)?;
    Ok(format!(
        "削除しました {}{}",
        entry_line(&Entry::from_manual(&removed)),
        after_write(paths, export)
    ))
}

fn cmd_list(flags: &Flags, paths: &Paths) -> Result<String, String> {
    let month = get(flags, "month");
    if let Some(m) = month {
        validate_month(m)?;
    }
    let ledger = store::load_ledger(paths)?;
    let shown: Vec<&Entry> = ledger
        .entries
        .iter()
        .filter(|e| month.is_none_or(|m| e.month() == m))
        .collect();

    let mut lines = vec![format!("# 家計台帳 {} ({} 件)", month.unwrap_or("全期間"), shown.len())];
    lines.extend(shown.iter().map(|e| entry_line(e)));
    lines.push(String::new());
    lines.push(format!(
        "現在残高: {}",
        ledger::balance_label(ledger::balance(&ledger.entries))
    ));
    lines.push(receipts_note(&ledger));
    Ok(lines.join("\n"))
}

fn cmd_balance(flags: &Flags, paths: &Paths) -> Result<String, String> {
    let month = get(flags, "month").map_or_else(clock::this_month, str::to_string);
    validate_month(&month)?;
    let ledger = store::load_ledger(paths)?;
    let balance = ledger::balance(&ledger.entries);

    let mut lines = vec![
        format!("現在残高: {}", ledger::balance_label(balance)),
        format!("家賃相殺: {}", rent_pass_label(ledger::rent_pass_suggestion(balance))),
        receipts_note(&ledger),
        String::new(),
        format!("# {month} の内訳"),
    ];

    let in_month: Vec<&Entry> = ledger.entries.iter().filter(|e| e.month() == month).collect();
    let mut shared_total = 0;
    for category in model::CATEGORIES {
        let total: i64 = in_month
            .iter()
            .filter(|e| e.kind == Kind::Shared && e.category == category)
            .map(|e| e.amount)
            .sum();
        if total > 0 {
            lines.push(format!("共同 {category}: {}円", ledger::group_thousands(total)));
            shared_total += total;
        }
    }
    lines.push(format!(
        "共同 合計: {}円（1 人あたり {}円）",
        ledger::group_thousands(shared_total),
        HalfYen(shared_total).display()
    ));
    for kind in [Kind::Individual, Kind::Adjustment, Kind::Transfer] {
        for payer in [Person::Satoshi, Person::Karin] {
            let total: i64 = in_month
                .iter()
                .filter(|e| e.kind == kind && e.payer == payer)
                .map(|e| e.amount)
                .sum();
            if total > 0 {
                lines.push(format!(
                    "{} {}: {}円",
                    kind.label(),
                    payer.label(),
                    ledger::group_thousands(total)
                ));
            }
        }
    }
    let month_delta: HalfYen = in_month.iter().map(|e| ledger::delta(e)).sum();
    lines.push(format!(
        "今月の残高変動: {}円（正 = 香琳 → 智史 が増えた）",
        month_delta.display()
    ));
    Ok(lines.join("\n"))
}

fn cmd_pass(flags: &Flags, paths: &Paths, export: bool) -> Result<String, String> {
    let month = get(flags, "month").map_or_else(clock::this_month, str::to_string);
    validate_month(&month)?;
    let dry_run = get(flags, "dry-run").is_some();
    let ledger = store::load_ledger(paths)?;
    let balance = ledger::balance(&ledger.entries);
    let suggestion = ledger::rent_pass_suggestion(balance);
    let item = format!("月次振込相殺 ({month})");

    let mut lines = vec![
        format!(
            "# 家賃相殺 {month}（月額 {}円）",
            ledger::group_thousands(ledger::MONTHLY_RENT)
        ),
        format!("現在残高: {}", ledger::balance_label(balance)),
        format!("提案: {}", rent_pass_label(suggestion)),
    ];

    if ledger
        .records
        .iter()
        .any(|r| r.kind == Kind::Transfer && r.item == item)
    {
        lines.push("この月の相殺はすでに記録済みです（変更なし）".to_string());
        return Ok(lines.join("\n"));
    }

    let amount = match (get(flags, "amount"), suggestion) {
        (Some(raw), _) => parse_amount(raw)?,
        (None, RentPass::Full(amount) | RentPass::Partial(amount)) => amount,
        (None, RentPass::None) => {
            lines.push("残高が 0 以下のため相殺しません（記録なし）".to_string());
            return Ok(lines.join("\n"));
        }
    };

    let record = ManualRecord {
        amount,
        category: "振込".to_string(),
        date: clock::month_end(&month)?,
        id: store::next_id(&ledger.records),
        item,
        kind: Kind::Transfer,
        memo: Some("智史から香琳への月次家賃振込を止めて相殺".to_string()),
        payer: Person::Karin,
        transfer_to: Some(Person::Satoshi),
    };
    let after = balance + ledger::delta(&Entry::from_manual(&record));
    lines.push(format!("記録後の残高: {}", ledger::balance_label(after)));

    if dry_run {
        lines.push("--dry-run: 記録は行いませんでした".to_string());
        return Ok(lines.join("\n"));
    }

    let mut records = ledger.records;
    records.push(record.clone());
    store::write_records(&paths.records, &records)?;
    lines.push(format!(
        "記録しました {}{}",
        entry_line(&Entry::from_manual(&record)),
        after_write(paths, export)
    ));
    Ok(lines.join("\n"))
}

fn cmd_receipt(flags: &Flags, paths: &Paths, export: bool) -> Result<String, String> {
    let reference = require(flags, "ref")?;
    let (month, source) = reference
        .split_once('/')
        .ok_or_else(|| format!("--ref は YYYY-MM/<source> 形式です: {reference}"))?;
    let payer = get(flags, "payer").map(Person::parse).transpose()?;
    let kind = get(flags, "kind").map(Kind::parse).transpose()?;
    match kind {
        None | Some(Kind::Shared | Kind::Individual) => {}
        Some(_) => return Err("レシートの種別は 共同 / 個別 のどちらかです".to_string()),
    }
    if payer.is_none() && kind.is_none() {
        return Err("--payer か --kind のどちらかを指定してください".to_string());
    }

    let receipt = store::update_receipt(&paths.receipts_archive, month, source, payer, kind)?;
    Ok(format!(
        "レシートを更新しました {}{}",
        entry_line(&Entry::from_receipt(month, &receipt)),
        after_write(paths, export)
    ))
}

fn cmd_export(paths: &Paths) -> Result<String, String> {
    let ledger = store::load_ledger(paths)?;
    let sql = sqlite_export::build_sql(&ledger.entries, &ledger.config.receipts_from, &clock::now_label());
    sqlite_export::export(&paths.sqlite, &sql, sqlite_export::sqlite3_runner)?;
    Ok(format!(
        "SQLite を書き出しました: {}（{} 件 / 残高 {}）",
        paths.sqlite.display(),
        ledger.entries.len(),
        ledger::balance_label(ledger::balance(&ledger.entries))
    ))
}

/// 書き込み後の SQLite 再生成。失敗しても正本の書き込みは済んでいるので警告にとどめる。
fn after_write(paths: &Paths, export: bool) -> String {
    if !export {
        return String::new();
    }
    match cmd_export(paths) {
        Ok(_) => String::new(),
        Err(error) => format!("\n(注意) SQLite 派生ビューの書き出しに失敗しました: {error}"),
    }
}

// --- 表示 -------------------------------------------------------------------

fn entry_line(entry: &Entry) -> String {
    let source = match entry.source {
        Source::Manual => format!("[{}]", entry.reference),
        Source::Receipt => format!("[R {}]", entry.reference),
    };
    let flow = match entry.transfer_to {
        Some(to) => format!("{}→{}", entry.payer.label(), to.label()),
        None => format!("{}払い", entry.payer.label()),
    };
    let memo = entry.memo.as_deref().map_or(String::new(), |m| format!(" ({m})"));
    format!(
        "{source} {} {} {} {} {}円 差分 {}{memo}",
        entry.date,
        entry.kind.label(),
        entry.category,
        flow,
        ledger::group_thousands(entry.amount),
        signed(ledger::delta(entry)),
    ) + &format!(" {}", entry.item)
}

fn receipts_note(ledger: &store::Ledger) -> String {
    format!(
        "レシート計上開始月: {}（それより前の {} 件は精算対象外）",
        ledger.config.receipts_from, ledger.excluded_receipts
    )
}

fn rent_pass_label(suggestion: RentPass) -> String {
    match suggestion {
        RentPass::Full(amount) => format!(
            "今月の家賃振込（{}円）を全額相殺できます",
            ledger::group_thousands(amount)
        ),
        RentPass::Partial(amount) => format!("{}円を一部相殺できます", ledger::group_thousands(amount)),
        RentPass::None => "相殺なし（残高が 0 以下）".to_string(),
    }
}

fn signed(value: HalfYen) -> String {
    if value.0 >= 0 {
        format!("+{}", value.display())
    } else {
        value.display()
    }
}

// --- フラグ解析 -------------------------------------------------------------

fn parse_flags(args: &[String]) -> Result<Flags, String> {
    let mut out = Vec::new();
    let mut index = 0;
    while index < args.len() {
        let arg = &args[index];
        let name = arg
            .strip_prefix("--")
            .ok_or_else(|| format!("予期しない引数: {arg}（--key value 形式）"))?;
        if let Some((key, value)) = name.split_once('=') {
            out.push((key.to_string(), value.to_string()));
            index += 1;
        } else if matches!(name, "dry-run" | "no-sqlite") {
            out.push((name.to_string(), "true".to_string()));
            index += 1;
        } else {
            let value = args
                .get(index + 1)
                .ok_or_else(|| format!("--{name} に値がありません"))?;
            out.push((name.to_string(), value.clone()));
            index += 2;
        }
    }
    Ok(out)
}

fn get<'a>(flags: &'a Flags, key: &str) -> Option<&'a str> {
    flags.iter().rev().find(|(k, _)| k == key).map(|(_, v)| v.as_str())
}

fn require<'a>(flags: &'a Flags, key: &str) -> Result<&'a str, String> {
    get(flags, key).ok_or_else(|| format!("--{key} は必須です"))
}

fn parse_amount(raw: &str) -> Result<i64, String> {
    let cleaned: String = raw.trim().chars().filter(|c| *c != '_' && *c != ',').collect();
    match cleaned.parse::<i64>() {
        Ok(value) if value > 0 => Ok(value),
        Ok(_) => Err("金額は正の整数です".to_string()),
        Err(_) => Err(format!("金額として解釈できません: {raw}")),
    }
}

fn validate_date(date: &str) -> Result<(), String> {
    let ok = date.len() == 10
        && store::is_month(&date[..7])
        && date.as_bytes()[7] == b'-'
        && date[8..].bytes().all(|b| b.is_ascii_digit());
    if ok {
        Ok(())
    } else {
        Err(format!("日付は YYYY-MM-DD 形式です: {date}"))
    }
}

fn validate_month(month: &str) -> Result<(), String> {
    if store::is_month(month) {
        Ok(())
    } else {
        Err(format!("月は YYYY-MM 形式です: {month}"))
    }
}

fn usage() -> String {
    [
        "household 家計台帳 CLI（手入力台帳 + レシート → 精算 → Grafana）",
        "",
        "使い方:",
        "  household add --kind <共同|個別|調整|振込> --payer <智史|香琳> --amount <円> \\",
        "                [--category <カテゴリ>] [--item <内容>] [--date YYYY-MM-DD] [--to <振込先>] [--memo <メモ>]",
        "  household delete --id <id>",
        "  household list [--month YYYY-MM]",
        "  household balance [--month YYYY-MM]",
        "  household pass [--month YYYY-MM] [--amount <円>] [--dry-run]",
        "  household receipt --ref YYYY-MM/<source> [--payer <智史|香琳>] [--kind <共同|個別>]",
        "  household export",
        "",
        "書き込み系は --no-sqlite で SQLite 派生ビューの再生成を抑止できる。",
        "精算: 残高 = 香琳が智史に払うべき額（共同 = 半額 / 個別・調整 = 全額 / 振込 = 移動）",
    ]
    .join("\n")
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::store::testutil::tmp_dir;
    use std::fs;

    fn flags(items: &[&str]) -> Flags {
        parse_flags(&items.iter().map(|s| s.to_string()).collect::<Vec<_>>()).unwrap()
    }

    fn add(paths: &Paths, items: &[&str]) -> Result<String, String> {
        cmd_add(&flags(items), paths, false)
    }

    #[test]
    fn add_validates_and_defaults() {
        let paths = Paths::under(&tmp_dir());
        let message = add(
            &paths,
            &[
                "--date",
                "2026-08-31",
                "--kind",
                "共同",
                "--payer",
                "智史",
                "--amount",
                "6,477",
                "--category",
                "電気",
            ],
        )
        .unwrap();
        assert!(
            message.contains("[1] 2026-08-31 共同 電気 智史払い 6,477円 差分 +3,238.5 電気"),
            "{message}"
        );

        assert!(add(&paths, &["--kind", "共同", "--payer", "智史", "--amount", "1"]).is_err()); // category 無し
        assert!(add(&paths, &["--kind", "振込", "--payer", "智史", "--amount", "1"]).is_err()); // to 無し
        assert!(add(
            &paths,
            &["--kind", "振込", "--payer", "智史", "--to", "智史", "--amount", "1"]
        )
        .is_err());
        assert!(add(
            &paths,
            &[
                "--kind",
                "共同",
                "--payer",
                "智史",
                "--to",
                "香琳",
                "--amount",
                "1",
                "--category",
                "食費"
            ]
        )
        .is_err());

        let transfer = add(
            &paths,
            &[
                "--date",
                "2026-09-01",
                "--kind",
                "振込",
                "--payer",
                "香琳",
                "--to",
                "智史",
                "--amount",
                "3238",
            ],
        )
        .unwrap();
        assert!(
            transfer.contains("振込 振込 香琳→智史 3,238円 差分 -3,238"),
            "{transfer}"
        );

        let records = store::read_records(&paths.records).unwrap();
        assert_eq!(records.len(), 2);
        assert_eq!(records[1].category, "振込");
    }

    #[test]
    fn balance_combines_manual_and_receipts() {
        let dir = tmp_dir();
        let paths = Paths::under(&dir);
        add(
            &paths,
            &[
                "--date",
                "2026-08-31",
                "--kind",
                "共同",
                "--payer",
                "智史",
                "--amount",
                "10000",
                "--category",
                "ガス",
            ],
        )
        .unwrap();
        let month_dir = dir.join("archive").join("2026-08");
        fs::create_dir_all(&month_dir).unwrap();
        fs::write(
            month_dir.join("transactions.jsonl"),
            r#"{"date":"2026-08-03","store":"サミット","category":"食費","total":2000,"file":"receipts/a.jpg","source":"A.JPG","added_at":"x"}
{"date":"2026-08-04","store":"薬局","category":"日用品","total":800,"file":"receipts/b.jpg","source":"B.JPG","added_at":"x"}
"#,
        )
        .unwrap();

        // 香琳が自分の物を智史に立て替えさせた → 智史払いの個別（100%）
        cmd_receipt(&flags(&["--ref", "2026-08/B.JPG", "--kind", "個別"]), &paths, false).unwrap();

        let out = cmd_balance(&flags(&["--month", "2026-08"]), &paths).unwrap();
        // 5000 + 1000 + 800 = 6800
        assert!(out.contains("現在残高: 香琳 → 智史 6,800円"), "{out}");
        assert!(out.contains("共同 ガス: 10,000円"));
        assert!(out.contains("共同 食費: 2,000円"));
        assert!(out.contains("共同 合計: 12,000円（1 人あたり 6,000円）"));
        assert!(out.contains("個別 智史: 800円"));
        assert!(out.contains("家賃相殺: 6,800円を一部相殺できます"));
    }

    #[test]
    fn receipt_rejects_transfer_kind() {
        let paths = Paths::under(&tmp_dir());
        assert!(cmd_receipt(&flags(&["--ref", "2026-08/A.JPG", "--kind", "振込"]), &paths, false).is_err());
        assert!(cmd_receipt(&flags(&["--ref", "2026-08/A.JPG"]), &paths, false).is_err());
    }

    #[test]
    fn pass_records_transfer_once() {
        let paths = Paths::under(&tmp_dir());
        // 残高 0 → 相殺しない
        let none = cmd_pass(&flags(&["--month", "2026-08"]), &paths, false).unwrap();
        assert!(none.contains("記録なし"));

        add(
            &paths,
            &[
                "--date",
                "2026-08-01",
                "--kind",
                "個別",
                "--payer",
                "智史",
                "--amount",
                "70000",
                "--category",
                "その他",
            ],
        )
        .unwrap();
        let dry = cmd_pass(&flags(&["--month", "2026-08", "--dry-run"]), &paths, false).unwrap();
        assert!(dry.contains("記録後の残高: 香琳 → 智史 6,300円"), "{dry}");
        assert_eq!(store::read_records(&paths.records).unwrap().len(), 1);

        let applied = cmd_pass(&flags(&["--month", "2026-08"]), &paths, false).unwrap();
        assert!(
            applied.contains("[2] 2026-08-31 振込 振込 香琳→智史 63,700円"),
            "{applied}"
        );

        let again = cmd_pass(&flags(&["--month", "2026-08"]), &paths, false).unwrap();
        assert!(again.contains("すでに記録済み"));
        assert_eq!(store::read_records(&paths.records).unwrap().len(), 2);
    }

    #[test]
    fn delete_and_flags() {
        let paths = Paths::under(&tmp_dir());
        add(
            &paths,
            &[
                "--date",
                "2026-08-01",
                "--kind",
                "共同",
                "--payer",
                "香琳",
                "--amount",
                "100",
                "--category",
                "水道",
            ],
        )
        .unwrap();
        assert!(cmd_delete(&flags(&["--id", "1"]), &paths, false)
            .unwrap()
            .contains("削除しました [1]"));
        assert!(cmd_delete(&flags(&["--id", "1"]), &paths, false).is_err());
        assert!(parse_flags(&["oops".to_string()]).is_err());
        assert_eq!(parse_amount("63,700").unwrap(), 63_700);
        assert!(parse_amount("0").is_err());
        assert!(validate_date("2026-8-1").is_err());
    }
}
