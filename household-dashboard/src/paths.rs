//! データの置き場所の解決。コード（git 管理）とデータ（git 管理外）を分ける。
//!
//! ```text
//! <repo>/household-data/            # git 管理外
//! ├── ledger/                       # Google Drive へのシンボリックリンク（正本）
//! │   ├── records.json              # 手入力台帳
//! │   └── config.json               # 設定（レシート計上開始月など）
//! └── household.db                  # Grafana 用 SQLite 派生ビュー（ローカル）
//! <repo>/shared-expense-data/archive/YYYY-MM/transactions.jsonl  # レシート（正本）
//! ```
//!
//! いずれも環境変数で上書きできる（テストは常に一時ディレクトリを渡す）。

use std::path::{Path, PathBuf};

fn env_path(name: &str) -> Option<PathBuf> {
    match std::env::var(name) {
        Ok(raw) if !raw.trim().is_empty() => Some(PathBuf::from(raw.trim())),
        _ => None,
    }
}

/// リポジトリルート（このクレートの 1 つ上）。
fn repo_root() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .map(Path::to_path_buf)
        .unwrap_or_else(|| PathBuf::from("."))
}

#[derive(Debug, Clone)]
pub struct Paths {
    pub records: PathBuf,
    pub config: PathBuf,
    pub sqlite: PathBuf,
    /// `shared-expense-data/archive`。
    pub receipts_archive: PathBuf,
}

impl Paths {
    pub fn resolve() -> Self {
        let data = env_path("HOUSEHOLD_DATA_DIR").unwrap_or_else(|| repo_root().join("household-data"));
        let shared = env_path("SHARED_EXPENSE_DATA").unwrap_or_else(|| repo_root().join("shared-expense-data"));
        Self {
            records: env_path("HOUSEHOLD_RECORDS_PATH").unwrap_or_else(|| data.join("ledger").join("records.json")),
            config: data.join("ledger").join("config.json"),
            sqlite: env_path("HOUSEHOLD_SQLITE_PATH").unwrap_or_else(|| data.join("household.db")),
            receipts_archive: shared.join("archive"),
        }
    }

    /// テスト用: 1 ディレクトリ配下にすべてを置く。
    #[cfg(test)]
    pub fn under(dir: &Path) -> Self {
        Self {
            records: dir.join("ledger").join("records.json"),
            config: dir.join("ledger").join("config.json"),
            sqlite: dir.join("household.db"),
            receipts_archive: dir.join("archive"),
        }
    }
}
