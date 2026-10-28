//! 精算ロジック（純粋関数）。旧 Elixir 版 `Dashboard.Household` の符号規約を移植したもの。
//!
//! 残高は常に「香琳が智史に払うべき額」（正なら智史の立て替え超過）。
//!
//! | 種別 | 智史が支払/送金 | 香琳が支払/送金 |
//! |---|---|---|
//! | 共同（50/50） | +金額/2 | -金額/2 |
//! | 個別・調整（100%） | +金額 | -金額 |
//! | 振込（移動） | +金額 | -金額 |
//!
//! 共同の折半で 0.5 円が出るため、内部では「半円」単位の整数で計算し、
//! 表示・書き出し時だけ円に戻す（浮動小数の誤差を持ち込まない）。

use crate::model::{Entry, Kind, Person};

/// 智史→香琳の月次家賃振込額。家賃相殺 1 回分の既定金額。
pub const MONTHLY_RENT: i64 = 63_700;

/// 半円単位の金額（2 で割ると円）。
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Default)]
pub struct HalfYen(pub i64);

impl HalfYen {
    pub fn as_yen_f64(self) -> f64 {
        self.0 as f64 / 2.0
    }

    /// 切り捨てた円（負なら 0 方向）。
    pub fn floor_yen(self) -> i64 {
        self.0.div_euclid(2)
    }

    /// 表示用の円（`1,234` / `1,234.5`、符号付き）。
    pub fn display(self) -> String {
        let negative = self.0 < 0;
        let abs = self.0.unsigned_abs();
        let whole = group_thousands((abs / 2) as i64);
        let body = if abs % 2 == 1 { format!("{whole}.5") } else { whole };
        if negative {
            format!("-{body}")
        } else {
            body
        }
    }
}

impl std::ops::Add for HalfYen {
    type Output = Self;
    fn add(self, rhs: Self) -> Self {
        Self(self.0 + rhs.0)
    }
}

impl std::iter::Sum for HalfYen {
    fn sum<I: Iterator<Item = Self>>(iter: I) -> Self {
        iter.fold(Self(0), |acc, x| acc + x)
    }
}

/// 1 件の残高変動。
pub fn delta(entry: &Entry) -> HalfYen {
    let sign = match entry.payer {
        Person::Satoshi => 1,
        Person::Karin => -1,
    };
    let half_units = match entry.kind {
        Kind::Shared => entry.amount,
        Kind::Individual | Kind::Adjustment | Kind::Transfer => entry.amount * 2,
    };
    HalfYen(sign * half_units)
}

/// 現在残高。
pub fn balance(entries: &[Entry]) -> HalfYen {
    entries.iter().map(delta).sum()
}

/// 日付ごとの累計残高（その日の全件適用後）。`entries` の並びに依存しない。
pub fn balance_series(entries: &[Entry]) -> Vec<(String, HalfYen)> {
    let mut sorted: Vec<&Entry> = entries.iter().collect();
    sorted.sort_by(|a, b| a.date.cmp(&b.date));

    let mut out: Vec<(String, HalfYen)> = Vec::new();
    let mut total = HalfYen(0);
    for entry in sorted {
        total = total + delta(entry);
        match out.last_mut() {
            Some((date, value)) if *date == entry.date => *value = total,
            _ => out.push((entry.date.clone(), total)),
        }
    }
    out
}

/// 家賃相殺の提案。
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum RentPass {
    /// 1 か月分まるごと相殺できる。
    Full(i64),
    /// 残高が 1 か月分に満たないので一部相殺（金額は残高の切り捨て円）。
    Partial(i64),
    /// 残高が 0 以下（智史側の借り）なので相殺しない。
    None,
}

pub fn rent_pass_suggestion(balance: HalfYen) -> RentPass {
    if balance.0 >= MONTHLY_RENT * 2 {
        RentPass::Full(MONTHLY_RENT)
    } else if balance.0 > 0 && balance.floor_yen() > 0 {
        RentPass::Partial(balance.floor_yen())
    } else {
        RentPass::None
    }
}

/// 残高の文言（`香琳 → 智史 1,234円`）。
pub fn balance_label(balance: HalfYen) -> String {
    if balance.0 > 0 {
        format!("香琳 → 智史 {}円", balance.display())
    } else if balance.0 < 0 {
        format!("智史 → 香琳 {}円", HalfYen(-balance.0).display())
    } else {
        "精算済み (0円)".to_string()
    }
}

pub fn group_thousands(value: i64) -> String {
    let negative = value < 0;
    let digits = value.unsigned_abs().to_string();
    let len = digits.len();
    let mut out = String::with_capacity(len + len / 3);
    for (index, ch) in digits.chars().enumerate() {
        if index > 0 && (len - index).is_multiple_of(3) {
            out.push(',');
        }
        out.push(ch);
    }
    if negative {
        format!("-{out}")
    } else {
        out
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::model::Source;

    fn entry(date: &str, kind: Kind, payer: Person, amount: i64) -> Entry {
        Entry {
            source: Source::Manual,
            reference: "1".to_string(),
            date: date.to_string(),
            item: "x".to_string(),
            category: "食費".to_string(),
            kind,
            payer,
            transfer_to: None,
            amount,
            memo: None,
        }
    }

    #[test]
    fn delta_follows_sign_convention() {
        assert_eq!(
            delta(&entry("2026-08-01", Kind::Shared, Person::Satoshi, 1001)),
            HalfYen(1001)
        );
        assert_eq!(
            delta(&entry("2026-08-01", Kind::Shared, Person::Karin, 1000)),
            HalfYen(-1000)
        );
        assert_eq!(
            delta(&entry("2026-08-01", Kind::Individual, Person::Satoshi, 300)),
            HalfYen(600)
        );
        assert_eq!(
            delta(&entry("2026-08-01", Kind::Adjustment, Person::Karin, 300)),
            HalfYen(-600)
        );
        assert_eq!(
            delta(&entry("2026-08-01", Kind::Transfer, Person::Karin, 63_700)),
            HalfYen(-127_400)
        );
    }

    #[test]
    fn half_yen_display() {
        assert_eq!(HalfYen(1001).display(), "500.5");
        assert_eq!(HalfYen(-2_000_000).display(), "-1,000,000");
        assert_eq!(balance_label(HalfYen(-3)), "智史 → 香琳 1.5円");
        assert_eq!(balance_label(HalfYen(0)), "精算済み (0円)");
    }

    #[test]
    fn balance_series_collapses_same_day() {
        let entries = vec![
            entry("2026-08-02", Kind::Shared, Person::Satoshi, 1000),
            entry("2026-08-01", Kind::Individual, Person::Karin, 100),
            entry("2026-08-02", Kind::Shared, Person::Satoshi, 1000),
        ];
        let series = balance_series(&entries);
        assert_eq!(
            series,
            vec![
                ("2026-08-01".to_string(), HalfYen(-200)),
                ("2026-08-02".to_string(), HalfYen(1800))
            ]
        );
        assert_eq!(balance(&entries), HalfYen(1800));
    }

    #[test]
    fn rent_pass_thresholds() {
        assert_eq!(rent_pass_suggestion(HalfYen(127_400)), RentPass::Full(63_700));
        assert_eq!(rent_pass_suggestion(HalfYen(1001)), RentPass::Partial(500));
        assert_eq!(rent_pass_suggestion(HalfYen(1)), RentPass::None);
        assert_eq!(rent_pass_suggestion(HalfYen(-10)), RentPass::None);
    }
}
