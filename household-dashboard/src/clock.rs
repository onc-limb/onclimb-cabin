//! JST の今日・今月と、月末日の計算（標準ライブラリのみ）。

use std::time::{SystemTime, UNIX_EPOCH};

const JST_OFFSET_SECS: i64 = 9 * 3600;

/// 今日（JST）`YYYY-MM-DD`。
pub fn today() -> String {
    let (y, m, d) = ymd_now();
    format!("{y:04}-{m:02}-{d:02}")
}

/// 今月（JST）`YYYY-MM`。
pub fn this_month() -> String {
    let (y, m, _) = ymd_now();
    format!("{y:04}-{m:02}")
}

/// 現在時刻（JST）`YYYY-MM-DD HH:MM:SS`。
pub fn now_label() -> String {
    let secs = now_secs() + JST_OFFSET_SECS;
    let (y, m, d) = days_to_ymd(secs.div_euclid(86_400));
    let rem = secs.rem_euclid(86_400);
    format!(
        "{y:04}-{m:02}-{d:02} {:02}:{:02}:{:02}",
        rem / 3600,
        rem % 3600 / 60,
        rem % 60
    )
}

fn now_secs() -> i64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs() as i64)
        .unwrap_or(0)
}

fn ymd_now() -> (i64, u32, u32) {
    days_to_ymd((now_secs() + JST_OFFSET_SECS).div_euclid(86_400))
}

/// `YYYY-MM` の月末日 `YYYY-MM-DD`。
pub fn month_end(month: &str) -> Result<String, String> {
    let (year, mon) = month
        .split_once('-')
        .and_then(|(y, m)| Some((y.parse::<i64>().ok()?, m.parse::<u32>().ok()?)))
        .filter(|(_, m)| (1..=12).contains(m))
        .ok_or_else(|| format!("月は YYYY-MM 形式です: {month}"))?;
    let leap = (year % 4 == 0 && year % 100 != 0) || year % 400 == 0;
    let last = match mon {
        2 if leap => 29,
        2 => 28,
        4 | 6 | 9 | 11 => 30,
        _ => 31,
    };
    Ok(format!("{year:04}-{mon:02}-{last:02}"))
}

/// UNIX エポックからの日数を (year, month, day) に変換する
/// （Howard Hinnant の `civil_from_days`）。
pub fn days_to_ymd(days: i64) -> (i64, u32, u32) {
    let z = days + 719_468;
    let era = if z >= 0 { z } else { z - 146_096 } / 146_097;
    let doe = z - era * 146_097;
    let yoe = (doe - doe / 1460 + doe / 36_524 - doe / 146_096) / 365;
    let year = yoe + era * 400;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let day = doy - (153 * mp + 2) / 5 + 1;
    let month = if mp < 10 { mp + 3 } else { mp - 9 };
    let year = if month <= 2 { year + 1 } else { year };
    (year, month as u32, day as u32)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn known_dates() {
        assert_eq!(days_to_ymd(0), (1970, 1, 1));
        assert_eq!(days_to_ymd(20_680), (2026, 8, 15));
    }

    #[test]
    fn month_end_handles_leap_years() {
        assert_eq!(month_end("2026-02").unwrap(), "2026-02-28");
        assert_eq!(month_end("2028-02").unwrap(), "2028-02-29");
        assert_eq!(month_end("2026-09").unwrap(), "2026-09-30");
        assert_eq!(month_end("2026-12").unwrap(), "2026-12-31");
        assert!(month_end("2026-13").is_err());
    }
}
