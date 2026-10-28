//! ファイル I/O。手入力台帳（records.json）・設定（config.json）・
//! レシート（transactions.jsonl）の読み込みと、アトミック書き込み（tmp + rename）。

use std::fs;
use std::io::Write;
use std::path::{Path, PathBuf};

use serde::{Deserialize, Serialize};
use serde_json::Value;

use crate::model::{Entry, Kind, ManualRecord, Person, Receipt};
use crate::paths::Paths;

// --- 手入力台帳 -------------------------------------------------------------

/// records.json を読む。無い/空なら空リスト。date → id 昇順で返す。
pub fn read_records(path: &Path) -> Result<Vec<ManualRecord>, String> {
    if !path.exists() {
        return Ok(Vec::new());
    }
    let raw = fs::read_to_string(path).map_err(|e| format!("{} の読み込みに失敗しました: {e}", path.display()))?;
    if raw.trim().is_empty() {
        return Ok(Vec::new());
    }
    let mut records: Vec<ManualRecord> =
        serde_json::from_str(&raw).map_err(|e| format!("{} を台帳として解釈できません: {e}", path.display()))?;
    sort_records(&mut records);
    Ok(records)
}

pub fn write_records(path: &Path, records: &[ManualRecord]) -> Result<(), String> {
    let mut sorted = records.to_vec();
    sort_records(&mut sorted);
    let body = serde_json::to_string_pretty(&sorted).map_err(|e| format!("台帳の JSON 化に失敗しました: {e}"))?;
    write_atomic(path, &format!("{body}\n"))
}

fn sort_records(records: &mut [ManualRecord]) {
    records.sort_by(|a, b| (a.date.as_str(), a.id).cmp(&(b.date.as_str(), b.id)));
}

pub fn next_id(records: &[ManualRecord]) -> i64 {
    records.iter().map(|r| r.id).max().map_or(1, |max| max + 1)
}

// --- 設定 -------------------------------------------------------------------

/// レシートを台帳に計上し始める月の既定値。
// ASSUMPTION: 2026-03〜05 のレシートは台帳に月次食費として計上済み。2026-07-26 の
// 「諸々立て替え精算」が 6・7 月分を含むか不明なため、確定するまで 2026-08 からとする
// （config.json の receiptsFrom で変更できる）。
pub const DEFAULT_RECEIPTS_FROM: &str = "2026-08";

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct Config {
    #[serde(default = "default_receipts_from")]
    pub receipts_from: String,
}

fn default_receipts_from() -> String {
    DEFAULT_RECEIPTS_FROM.to_string()
}

impl Default for Config {
    fn default() -> Self {
        Self {
            receipts_from: default_receipts_from(),
        }
    }
}

pub fn read_config(path: &Path) -> Result<Config, String> {
    if !path.exists() {
        return Ok(Config::default());
    }
    let raw = fs::read_to_string(path).map_err(|e| format!("{} の読み込みに失敗しました: {e}", path.display()))?;
    serde_json::from_str(&raw).map_err(|e| format!("{} を設定として解釈できません: {e}", path.display()))
}

// --- レシート ---------------------------------------------------------------

/// 月ディレクトリ（`YYYY-MM`）ごとのレシート。
pub struct ReceiptMonth {
    pub month: String,
    pub receipts: Vec<Receipt>,
}

/// `archive/YYYY-MM/transactions.jsonl` を全月読む。壊れた行があれば停止する
/// （黙って飛ばすと精算額が狂うため）。
pub fn read_receipts(archive: &Path) -> Result<Vec<ReceiptMonth>, String> {
    if !archive.exists() {
        return Ok(Vec::new());
    }
    let mut months: Vec<String> = fs::read_dir(archive)
        .map_err(|e| format!("{} を開けません: {e}", archive.display()))?
        .filter_map(|entry| entry.ok())
        .map(|entry| entry.file_name().to_string_lossy().to_string())
        .filter(|name| is_month(name))
        .collect();
    months.sort();

    let mut out = Vec::new();
    for month in months {
        let path = archive.join(&month).join("transactions.jsonl");
        if !path.exists() {
            continue;
        }
        let raw = fs::read_to_string(&path).map_err(|e| format!("{} の読み込みに失敗しました: {e}", path.display()))?;
        let mut receipts = Vec::new();
        for (index, line) in raw.lines().enumerate() {
            if line.trim().is_empty() {
                continue;
            }
            let receipt: Receipt = serde_json::from_str(line)
                .map_err(|e| format!("{}:{} を解釈できません: {e}", path.display(), index + 1))?;
            if receipt.total <= 0 {
                return Err(format!("{}:{} の total が正ではありません", path.display(), index + 1));
            }
            receipts.push(receipt);
        }
        out.push(ReceiptMonth { month, receipts });
    }
    Ok(out)
}

/// レシート 1 件の支払者・種別を書き換える。対象行以外は元の文字列のまま残す。
pub fn update_receipt(
    archive: &Path,
    month: &str,
    source: &str,
    payer: Option<Person>,
    kind: Option<Kind>,
) -> Result<Receipt, String> {
    if !is_month(month) {
        return Err(format!("月は YYYY-MM 形式です: {month}"));
    }
    let path = archive.join(month).join("transactions.jsonl");
    let raw = fs::read_to_string(&path).map_err(|e| format!("{} の読み込みに失敗しました: {e}", path.display()))?;

    let mut updated: Option<Receipt> = None;
    let mut lines: Vec<String> = Vec::new();
    for (index, line) in raw.lines().enumerate() {
        if line.trim().is_empty() {
            continue;
        }
        let mut value: Value = serde_json::from_str(line)
            .map_err(|e| format!("{}:{} を解釈できません: {e}", path.display(), index + 1))?;
        if value.get("source").and_then(Value::as_str) != Some(source) {
            lines.push(line.to_string());
            continue;
        }
        if updated.is_some() {
            return Err(format!("source={source} のレシートが {month} に複数あります"));
        }
        let object = value
            .as_object_mut()
            .ok_or_else(|| format!("{}:{} が JSON オブジェクトではありません", path.display(), index + 1))?;
        if let Some(payer) = payer {
            object.insert("payer".to_string(), Value::String(payer.label().to_string()));
        }
        if let Some(kind) = kind {
            object.insert("kind".to_string(), Value::String(kind.label().to_string()));
        }
        let new_line = serde_json::to_string(&value).map_err(|e| format!("JSON 化に失敗しました: {e}"))?;
        updated = Some(serde_json::from_str(&new_line).map_err(|e| format!("書き換え後の行が不正です: {e}"))?);
        lines.push(new_line);
    }

    let receipt = updated.ok_or_else(|| format!("{month} に source={source} のレシートがありません"))?;
    write_atomic(&path, &format!("{}\n", lines.join("\n")))?;
    Ok(receipt)
}

// --- 統合 -------------------------------------------------------------------

/// 精算対象の全エントリ（手入力 + 計上開始月以降のレシート）。
pub struct Ledger {
    pub entries: Vec<Entry>,
    pub records: Vec<ManualRecord>,
    pub config: Config,
    /// 計上開始月より前で精算対象外にしたレシートの件数。
    pub excluded_receipts: usize,
}

pub fn load_ledger(paths: &Paths) -> Result<Ledger, String> {
    let records = read_records(&paths.records)?;
    let config = read_config(&paths.config)?;
    let months = read_receipts(&paths.receipts_archive)?;

    let mut entries: Vec<Entry> = records.iter().map(Entry::from_manual).collect();
    let mut excluded_receipts = 0;
    for month in &months {
        for receipt in &month.receipts {
            if receipt.date.as_str() < config.receipts_from.as_str() {
                excluded_receipts += 1;
            } else {
                entries.push(Entry::from_receipt(&month.month, receipt));
            }
        }
    }
    entries.sort_by(|a, b| a.date.cmp(&b.date));

    Ok(Ledger {
        entries,
        records,
        config,
        excluded_receipts,
    })
}

// --- 共通 -------------------------------------------------------------------

pub fn is_month(value: &str) -> bool {
    value.len() == 7
        && value
            .bytes()
            .enumerate()
            .all(|(i, b)| if i == 4 { b == b'-' } else { b.is_ascii_digit() })
}

pub fn write_atomic(path: &Path, body: &str) -> Result<(), String> {
    if let Some(parent) = path.parent() {
        if !parent.as_os_str().is_empty() {
            fs::create_dir_all(parent).map_err(|e| format!("{} を作成できません: {e}", parent.display()))?;
        }
    }
    let tmp = tmp_path(path);
    {
        let mut file = fs::File::create(&tmp).map_err(|e| format!("一時ファイルを作成できません: {e}"))?;
        file.write_all(body.as_bytes())
            .map_err(|e| format!("一時ファイルへの書き込みに失敗しました: {e}"))?;
        let _ = file.sync_all();
    }
    fs::rename(&tmp, path).map_err(|e| {
        let _ = fs::remove_file(&tmp);
        format!("{} への置き換えに失敗しました: {e}", path.display())
    })
}

pub fn tmp_path(path: &Path) -> PathBuf {
    let mut name = path.as_os_str().to_os_string();
    name.push(format!(".tmp.{}", std::process::id()));
    PathBuf::from(name)
}

#[cfg(test)]
pub mod testutil {
    use std::path::PathBuf;
    use std::sync::atomic::{AtomicU64, Ordering};

    static COUNTER: AtomicU64 = AtomicU64::new(0);

    pub fn tmp_dir() -> PathBuf {
        let n = COUNTER.fetch_add(1, Ordering::SeqCst);
        let dir = std::env::temp_dir().join(format!("household_test_{}_{}", std::process::id(), n));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).unwrap();
        dir
    }
}

#[cfg(test)]
mod tests {
    use super::testutil::tmp_dir;
    use super::*;

    const RECEIPT_A: &str = r#"{"date":"2026-07-31","store":"サミット","category":"食費","total":1000,"file":"receipts/a.jpg","source":"A.JPG","added_at":"x"}"#;
    const RECEIPT_B: &str = r#"{"date":"2026-08-01","store":"まいばすけっと","category":"食費","total":500,"file":"receipts/b.jpg","source":"B.JPG","added_at":"x"}"#;

    fn write_month(dir: &Path, month: &str, lines: &[&str]) {
        let path = dir.join("archive").join(month).join("transactions.jsonl");
        fs::create_dir_all(path.parent().unwrap()).unwrap();
        fs::write(path, format!("{}\n", lines.join("\n"))).unwrap();
    }

    #[test]
    fn records_round_trip_sorted() {
        let dir = tmp_dir();
        let path = dir.join("records.json");
        assert!(read_records(&path).unwrap().is_empty());
        let raw = r#"[{"amount":1,"category":"電気","date":"2026-08-02","id":1,"item":"電気","kind":"共同","memo":null,"payer":"智史","transferTo":null},
                      {"amount":2,"category":"ガス","date":"2026-08-01","id":2,"item":"ガス","kind":"共同","memo":null,"payer":"香琳","transferTo":null}]"#;
        fs::write(&path, raw).unwrap();
        let records = read_records(&path).unwrap();
        assert_eq!(records[0].id, 2);
        write_records(&path, &records).unwrap();
        assert_eq!(read_records(&path).unwrap(), records);
        assert_eq!(next_id(&records), 3);
    }

    #[test]
    fn ledger_excludes_receipts_before_cutover() {
        let dir = tmp_dir();
        write_month(&dir, "2026-07", &[RECEIPT_A]);
        write_month(&dir, "2026-08", &[RECEIPT_B]);
        let ledger = load_ledger(&Paths::under(&dir)).unwrap();
        assert_eq!(ledger.excluded_receipts, 1);
        assert_eq!(ledger.entries.len(), 1);
        assert_eq!(ledger.entries[0].item, "まいばすけっと");
    }

    #[test]
    fn config_overrides_cutover() {
        let dir = tmp_dir();
        write_month(&dir, "2026-07", &[RECEIPT_A]);
        let paths = Paths::under(&dir);
        fs::create_dir_all(paths.config.parent().unwrap()).unwrap();
        fs::write(&paths.config, r#"{"receiptsFrom":"2026-06"}"#).unwrap();
        let ledger = load_ledger(&paths).unwrap();
        assert_eq!(ledger.excluded_receipts, 0);
        assert_eq!(ledger.entries.len(), 1);
    }

    #[test]
    fn update_receipt_touches_only_target_line() {
        let dir = tmp_dir();
        write_month(&dir, "2026-08", &[RECEIPT_A, RECEIPT_B]);
        let archive = dir.join("archive");
        let receipt = update_receipt(
            &archive,
            "2026-08",
            "B.JPG",
            Some(Person::Karin),
            Some(Kind::Individual),
        )
        .unwrap();
        assert_eq!(receipt.payer, Some(Person::Karin));
        assert_eq!(receipt.kind, Some(Kind::Individual));

        let raw = fs::read_to_string(archive.join("2026-08").join("transactions.jsonl")).unwrap();
        let lines: Vec<&str> = raw.lines().collect();
        assert_eq!(lines[0], RECEIPT_A);
        assert!(lines[1].starts_with(r#"{"date":"2026-08-01","store":"まいばすけっと""#));
        assert!(lines[1].ends_with(r#""added_at":"x","payer":"香琳","kind":"個別"}"#));

        assert!(update_receipt(&archive, "2026-08", "NONE.JPG", Some(Person::Karin), None).is_err());
    }

    #[test]
    fn broken_receipt_line_stops() {
        let dir = tmp_dir();
        write_month(&dir, "2026-08", &[RECEIPT_B, "{broken"]);
        assert!(read_receipts(&dir.join("archive")).is_err());
    }
}
