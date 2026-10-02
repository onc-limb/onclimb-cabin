//! 台帳の値型。手入力台帳（records.json）とレシート（transactions.jsonl）の
//! 2 系統を、精算計算用の共通形 `Entry` にそろえる。
//!
//! 値は旧 Elixir 版（personal-dashboard の `Dashboard.Household`）と同じ日本語表記で
//! 保存する（records.json の互換性を保つため）。

use serde::{Deserialize, Serialize};

/// 夫婦 2 名。
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum Person {
    #[serde(rename = "智史")]
    Satoshi,
    #[serde(rename = "香琳")]
    Karin,
}

impl Person {
    pub fn parse(raw: &str) -> Result<Self, String> {
        match raw.trim() {
            "智史" | "satoshi" => Ok(Self::Satoshi),
            "香琳" | "karin" => Ok(Self::Karin),
            other => Err(format!("支払者は 智史 / 香琳 のいずれかです: {other}")),
        }
    }

    pub fn label(self) -> &'static str {
        match self {
            Self::Satoshi => "智史",
            Self::Karin => "香琳",
        }
    }
}

/// 精算上の種別。
///
/// - 共同: 折半（50/50）
/// - 個別: 全額立て替え（100% 相手負担）
/// - 調整: 個別と同じ全額移動（手動の残高調整）
/// - 振込: 現金の移動（家賃相殺もここ）
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum Kind {
    #[serde(rename = "共同")]
    Shared,
    #[serde(rename = "個別")]
    Individual,
    #[serde(rename = "振込")]
    Transfer,
    #[serde(rename = "調整")]
    Adjustment,
}

impl Kind {
    pub fn parse(raw: &str) -> Result<Self, String> {
        match raw.trim() {
            "共同" | "shared" => Ok(Self::Shared),
            "個別" | "立て替え" | "individual" | "advance" => Ok(Self::Individual),
            "振込" | "transfer" => Ok(Self::Transfer),
            "調整" | "adjustment" => Ok(Self::Adjustment),
            other => Err(format!("種別は 共同 / 個別 / 振込 / 調整 のいずれかです: {other}")),
        }
    }

    pub fn label(self) -> &'static str {
        match self {
            Self::Shared => "共同",
            Self::Individual => "個別",
            Self::Transfer => "振込",
            Self::Adjustment => "調整",
        }
    }
}

/// カテゴリ（表示順）。手入力台帳の旧カテゴリとレシートのカテゴリの和集合。
pub const CATEGORIES: [&str; 13] = [
    "電気",
    "水道",
    "ガス",
    "食費",
    "外食",
    "ビール",
    "日用品",
    "交通費",
    "旅行",
    "家具",
    "その他",
    "振込",
    "初期残高",
];

pub fn validate_category(raw: &str) -> Result<String, String> {
    let trimmed = raw.trim();
    if CATEGORIES.contains(&trimmed) {
        Ok(trimmed.to_string())
    } else {
        Err(format!(
            "カテゴリは {} のいずれかです: {trimmed}",
            CATEGORIES.join(" / ")
        ))
    }
}

/// 手入力台帳 records.json の 1 レコード。
///
/// フィールドの宣言順はキーのアルファベット順（既存 records.json の書式に合わせる）。
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct ManualRecord {
    pub amount: i64,
    pub category: String,
    pub date: String,
    pub id: i64,
    pub item: String,
    pub kind: Kind,
    pub memo: Option<String>,
    pub payer: Person,
    #[serde(rename = "transferTo")]
    pub transfer_to: Option<Person>,
}

/// レシート（transactions.jsonl の 1 行）。精算に必要な項目だけを読む。
#[derive(Debug, Clone, PartialEq, Deserialize)]
pub struct Receipt {
    pub date: String,
    pub store: String,
    pub category: String,
    pub total: i64,
    pub source: String,
    /// 未記入なら智史（既定値）。
    #[serde(default)]
    pub payer: Option<Person>,
    /// 未記入なら共同（既定値）。
    #[serde(default)]
    pub kind: Option<Kind>,
}

/// レシートの支払者の既定値。
// ASSUMPTION: 2026-10 時点のレシートは全件智史の支払い（台帳の月次食費が智史・共同で
// 計上されていた実態から）。例外は `household receipt` で後から直す。
pub const RECEIPT_DEFAULT_PAYER: Person = Person::Satoshi;
pub const RECEIPT_DEFAULT_KIND: Kind = Kind::Shared;

/// 精算計算の共通形。
#[derive(Debug, Clone, PartialEq)]
pub struct Entry {
    pub source: Source,
    /// 手入力は id、レシートは `YYYY-MM/<source>`。
    pub reference: String,
    pub date: String,
    pub item: String,
    pub category: String,
    pub kind: Kind,
    pub payer: Person,
    pub transfer_to: Option<Person>,
    pub amount: i64,
    pub memo: Option<String>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Source {
    Manual,
    Receipt,
}

impl Source {
    pub fn as_key(self) -> &'static str {
        match self {
            Self::Manual => "manual",
            Self::Receipt => "receipt",
        }
    }
}

impl Entry {
    pub fn month(&self) -> &str {
        &self.date[..7]
    }

    pub fn from_manual(record: &ManualRecord) -> Self {
        Self {
            source: Source::Manual,
            reference: record.id.to_string(),
            date: record.date.clone(),
            item: record.item.clone(),
            category: record.category.clone(),
            kind: record.kind,
            payer: record.payer,
            transfer_to: record.transfer_to,
            amount: record.amount,
            memo: record.memo.clone(),
        }
    }

    pub fn from_receipt(month: &str, receipt: &Receipt) -> Self {
        Self {
            source: Source::Receipt,
            reference: format!("{month}/{}", receipt.source),
            date: receipt.date.clone(),
            item: receipt.store.clone(),
            category: receipt.category.clone(),
            kind: receipt.kind.unwrap_or(RECEIPT_DEFAULT_KIND),
            payer: receipt.payer.unwrap_or(RECEIPT_DEFAULT_PAYER),
            transfer_to: None,
            amount: receipt.total,
            memo: None,
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn manual_record_round_trips_existing_format() {
        let raw = r#"{"amount":63700,"category":"振込","date":"2025-09-30","id":52,"item":"月次振込相殺 (2025-09)","kind":"振込","memo":null,"payer":"香琳","transferTo":"智史"}"#;
        let record: ManualRecord = serde_json::from_str(raw).unwrap();
        assert_eq!(record.kind, Kind::Transfer);
        assert_eq!(record.payer, Person::Karin);
        assert_eq!(record.transfer_to, Some(Person::Satoshi));
        assert_eq!(serde_json::to_string(&record).unwrap(), raw);
    }

    #[test]
    fn receipt_defaults_to_satoshi_shared() {
        let raw = r#"{"date":"2026-08-01","store":"サミット","category":"食費","total":1392,"file":"receipts/a.jpg","source":"IMG_1.JPG","added_at":"x"}"#;
        let receipt: Receipt = serde_json::from_str(raw).unwrap();
        let entry = Entry::from_receipt("2026-08", &receipt);
        assert_eq!(entry.payer, Person::Satoshi);
        assert_eq!(entry.kind, Kind::Shared);
        assert_eq!(entry.reference, "2026-08/IMG_1.JPG");
    }

    #[test]
    fn parse_accepts_japanese_and_english() {
        assert_eq!(Kind::parse("立て替え").unwrap(), Kind::Individual);
        assert_eq!(Kind::parse("shared").unwrap(), Kind::Shared);
        assert_eq!(Person::parse("karin").unwrap(), Person::Karin);
        assert!(Kind::parse("bogus").is_err());
        assert!(validate_category("家賃").is_err());
    }
}
