//! Grafana 閲覧用の SQLite 派生ビュー（正本 → SQLite の一方向）。
//!
//! 正本（records.json と transactions.jsonl）から毎回作り直す。SQLite への書き込みは
//! `sqlite3` CLI へ SQL を流し込む（ネイティブライブラリのビルドを避けるため）。
//! 一時ファイルへ作ってから rename するので、Grafana が書き込み途中を読むことはない。
//!
//! テーブル:
//! - `household_entries`  … 精算対象の全明細（source = manual / receipt）
//! - `household_balance`  … 日付ごとの累計残高
//! - `household_summary`  … 現在残高・家賃相殺の提案・計上開始月・生成時刻（1 行）

use std::path::Path;

use crate::ledger::{self, RentPass};
use crate::model::Entry;
use crate::store;

pub fn build_sql(entries: &[Entry], receipts_from: &str, generated_at: &str) -> String {
    let mut out = String::from("BEGIN TRANSACTION;\n");

    out.push_str(
        "CREATE TABLE household_entries (source TEXT NOT NULL, ref TEXT NOT NULL, date TEXT NOT NULL, \
         month TEXT NOT NULL, item TEXT NOT NULL, category TEXT NOT NULL, kind TEXT NOT NULL, \
         payer TEXT NOT NULL, transfer_to TEXT, amount INTEGER NOT NULL, delta REAL NOT NULL, memo TEXT);\n",
    );
    for entry in entries {
        out.push_str(&format!(
            "INSERT INTO household_entries VALUES ('{}', {}, {}, {}, {}, {}, '{}', '{}', {}, {}, {}, {});\n",
            entry.source.as_key(),
            quote(&entry.reference),
            quote(&entry.date),
            quote(entry.month()),
            quote(&entry.item),
            quote(&entry.category),
            entry.kind.label(),
            entry.payer.label(),
            entry.transfer_to.map_or("NULL".to_string(), |p| quote(p.label())),
            entry.amount,
            ledger::delta(entry).as_yen_f64(),
            entry.memo.as_deref().map_or("NULL".to_string(), quote),
        ));
    }

    out.push_str("CREATE TABLE household_balance (date TEXT NOT NULL, balance REAL NOT NULL);\n");
    for (date, balance) in ledger::balance_series(entries) {
        out.push_str(&format!(
            "INSERT INTO household_balance VALUES ({}, {});\n",
            quote(&date),
            balance.as_yen_f64()
        ));
    }

    let balance = ledger::balance(entries);
    let (suggestion, suggestion_amount) = match ledger::rent_pass_suggestion(balance) {
        RentPass::Full(amount) => ("全額相殺できます", amount),
        RentPass::Partial(amount) => ("一部相殺できます", amount),
        RentPass::None => ("相殺なし", 0),
    };
    out.push_str(
        "CREATE TABLE household_summary (balance REAL NOT NULL, balance_label TEXT NOT NULL, \
         rent_pass TEXT NOT NULL, rent_pass_amount INTEGER NOT NULL, receipts_from TEXT NOT NULL, \
         generated_at TEXT NOT NULL);\n",
    );
    out.push_str(&format!(
        "INSERT INTO household_summary VALUES ({}, {}, '{}', {}, {}, {});\n",
        balance.as_yen_f64(),
        quote(&ledger::balance_label(balance)),
        suggestion,
        suggestion_amount,
        quote(receipts_from),
        quote(generated_at),
    ));

    out.push_str("COMMIT;\n");
    out
}

/// SQLite を作り直す。`run` は (出力先, SQL) を受け取って書き込む（テストで差し替え可）。
pub fn export<F>(path: &Path, sql: &str, run: F) -> Result<(), String>
where
    F: FnOnce(&Path, &str) -> Result<(), String>,
{
    let tmp = store::tmp_path(path);
    let _ = std::fs::remove_file(&tmp);
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent).map_err(|e| format!("{} を作成できません: {e}", parent.display()))?;
    }
    run(&tmp, sql).inspect_err(|_| {
        let _ = std::fs::remove_file(&tmp);
    })?;
    std::fs::rename(&tmp, path).map_err(|e| format!("{} への置き換えに失敗しました: {e}", path.display()))
}

/// `sqlite3 <path>` に SQL を標準入力から流し込む。
pub fn sqlite3_runner(path: &Path, sql: &str) -> Result<(), String> {
    use std::io::Write;
    use std::process::{Command, Stdio};

    let mut child = Command::new("sqlite3")
        .arg(path)
        .stdin(Stdio::piped())
        .stdout(Stdio::null())
        .stderr(Stdio::piped())
        .spawn()
        .map_err(|e| format!("sqlite3 を起動できません: {e}"))?;
    if let Some(mut stdin) = child.stdin.take() {
        stdin
            .write_all(sql.as_bytes())
            .map_err(|e| format!("sqlite3 への書き込みに失敗しました: {e}"))?;
    }
    let output = child
        .wait_with_output()
        .map_err(|e| format!("sqlite3 の終了待ちに失敗しました: {e}"))?;
    if output.status.success() {
        Ok(())
    } else {
        Err(format!(
            "sqlite3 が失敗しました: {}",
            String::from_utf8_lossy(&output.stderr).trim()
        ))
    }
}

fn quote(value: &str) -> String {
    format!("'{}'", value.replace('\'', "''"))
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::model::{Kind, Person, Source};
    use crate::store::testutil::tmp_dir;

    fn sample() -> Vec<Entry> {
        vec![
            Entry {
                source: Source::Manual,
                reference: "1".to_string(),
                date: "2026-08-31".to_string(),
                item: "電気".to_string(),
                category: "電気".to_string(),
                kind: Kind::Shared,
                payer: Person::Satoshi,
                transfer_to: None,
                amount: 1001,
                memo: Some("It's".to_string()),
            },
            Entry {
                source: Source::Receipt,
                reference: "2026-08/A.JPG".to_string(),
                date: "2026-08-01".to_string(),
                item: "サミット".to_string(),
                category: "食費".to_string(),
                kind: Kind::Individual,
                payer: Person::Karin,
                transfer_to: None,
                amount: 300,
                memo: None,
            },
        ]
    }

    #[test]
    fn build_sql_contains_rows_and_summary() {
        let sql = build_sql(&sample(), "2026-08", "2026-10-02 12:00:00");
        assert!(sql.contains("'It''s'"));
        assert!(sql.contains("INSERT INTO household_entries VALUES ('receipt', '2026-08/A.JPG'"));
        // +500.5 - 300 = 200.5
        assert!(sql
            .contains("INSERT INTO household_summary VALUES (200.5, '香琳 → 智史 200.5円', '一部相殺できます', 200,"));
        assert_eq!(sql, build_sql(&sample(), "2026-08", "2026-10-02 12:00:00"));
    }

    #[test]
    fn export_writes_real_sqlite() {
        let dir = tmp_dir();
        let path = dir.join("household.db");
        let sql = build_sql(&sample(), "2026-08", "now");
        export(&path, &sql, sqlite3_runner).unwrap();
        // 2 回目も既存ファイルを置き換えて成功する（DROP 不要）。
        export(&path, &sql, sqlite3_runner).unwrap();

        let output = std::process::Command::new("sqlite3")
            .arg(&path)
            .arg("SELECT COUNT(*), SUM(delta) FROM household_entries;")
            .output()
            .unwrap();
        assert_eq!(String::from_utf8_lossy(&output.stdout).trim(), "2|200.5");
    }
}
