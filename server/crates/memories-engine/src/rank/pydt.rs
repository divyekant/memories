//! The parts of Python 3.11 `datetime` that the rank rules use.
//!
//! `fromisoformat` is a port of the C module (`_datetimemodule.c` of 3.11),
//! not of the pure Python module: the two accept different strings. The parser
//! works on the UTF-8 bytes, as the C code does. A read past the end gives 0,
//! as the NUL terminator of the C string does.

/// Microseconds in one day.
pub const DAY_US: i64 = 86_400_000_000;
/// Ordinal of 9999-12-31 (`date.max.toordinal()`).
pub const MAX_ORDINAL: i64 = 3_652_059;

/// A Python `datetime`: a proleptic Gregorian ordinal (0001-01-01 is 1), the
/// time of day, and the UTC offset of an aware value.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct PyDateTime {
    pub ordinal: i64,
    pub day_us: i64,
    pub offset_us: Option<i64>,
}

pub fn is_leap(year: i64) -> bool {
    year % 4 == 0 && (year % 100 != 0 || year % 400 == 0)
}

pub fn days_in_month(year: i64, month: i64) -> i64 {
    match month {
        2 if is_leap(year) => 29,
        2 => 28,
        4 | 6 | 9 | 11 => 30,
        _ => 31,
    }
}

/// `ymd_to_ord` of the C module. The divisions truncate, as in C.
pub fn ymd_to_ord(year: i64, month: i64, day: i64) -> i64 {
    let y = year - 1;
    let before_year = y * 365 + y / 4 - y / 100 + y / 400;
    let before_month: i64 = (1..month).map(|m| days_in_month(year, m)).sum();
    before_year + before_month + day
}

/// (year, month, day) of an ordinal in `1..=MAX_ORDINAL`.
pub fn ord_to_ymd(ordinal: i64) -> (i64, i64, i64) {
    let mut n = ordinal - 1;
    let n400 = n / 146_097;
    n %= 146_097;
    let n100 = n / 36_524;
    n %= 36_524;
    let n4 = n / 1_461;
    n %= 1_461;
    let n1 = n / 365;
    n %= 365;
    let mut year = n400 * 400 + n100 * 100 + n4 * 4 + n1 + 1;
    if n1 == 4 || n100 == 4 {
        year -= 1;
        return (year, 12, 31);
    }
    let mut month = 1;
    while n >= days_in_month(year, month) {
        n -= days_in_month(year, month);
        month += 1;
    }
    (year, month, n + 1)
}

/// `date.weekday()`: Monday is 0.
pub fn weekday(ordinal: i64) -> i64 {
    (ordinal + 6) % 7
}

/// The ordinal of a valid date, or `None` (Python raises `ValueError`).
pub fn checked_ord(year: i64, month: i64, day: i64) -> Option<i64> {
    let valid = (1..=9999).contains(&year)
        && (1..=12).contains(&month)
        && day >= 1
        && day <= days_in_month(year, month);
    valid.then(|| ymd_to_ord(year, month, day))
}

impl PyDateTime {
    pub fn fields(&self) -> [i64; 7] {
        let (y, m, d) = ord_to_ymd(self.ordinal);
        let s = self.day_us / 1_000_000;
        [
            y,
            m,
            d,
            s / 3600,
            s / 60 % 60,
            s % 60,
            self.day_us % 1_000_000,
        ]
    }

    fn local_us(&self) -> i64 {
        (self.ordinal - 1) * DAY_US + self.day_us
    }

    /// Microseconds since 0001-01-01 UTC; a naive value counts as UTC.
    pub fn utc_us(&self) -> i64 {
        self.local_us() - self.offset_us.unwrap_or(0)
    }

    /// `ts.replace(tzinfo=timezone.utc)` for a naive value.
    pub fn or_utc(self) -> Self {
        Self {
            offset_us: Some(self.offset_us.unwrap_or(0)),
            ..self
        }
    }

    /// `self < other`. `None` where Python raises `TypeError` (naive and aware).
    pub fn lt(&self, other: &Self) -> Option<bool> {
        match (self.offset_us, other.offset_us) {
            (Some(_), Some(_)) => Some(self.utc_us() < other.utc_us()),
            (None, None) => Some(self.local_us() < other.local_us()),
            _ => None,
        }
    }

    /// `(self - earlier).total_seconds()` for two aware values.
    pub fn seconds_since(&self, earlier: &Self) -> f64 {
        let us = self.utc_us() - earlier.utc_us();
        // Python divides the integer exactly; an f64 holds it below 2**53.
        if us.unsigned_abs() < 1 << 53 {
            us as f64 / 1e6
        } else {
            format!("{us}e-6").parse().unwrap_or(f64::NAN)
        }
    }

    /// `datetime.isoformat()` of an aware value with a zero offset.
    pub fn isoformat_utc(&self) -> String {
        let [y, mo, d, h, mi, s, us] = self.fields();
        let frac = if us == 0 {
            String::new()
        } else {
            format!(".{us:06}")
        };
        format!("{y:04}-{mo:02}-{d:02}T{h:02}:{mi:02}:{s:02}{frac}+00:00")
    }

    /// `datetime.fromisoformat` of Python 3.11 (the C module).
    pub fn fromisoformat(text: &str) -> Option<Self> {
        if text.chars().count() < 7 {
            return None;
        }
        let b = text.as_bytes();
        let sep = find_separator(b)?;
        let (year, month, day) = parse_date(b, sep)?;
        let mut time = Time::default();
        if b.len() > sep {
            let mut start = sep + 1;
            // The separator is one character, which can take several bytes.
            while start < b.len() && b[start] & 0xC0 == 0x80 {
                start += 1;
            }
            time = parse_time(&b[start..])?;
        }
        let ordinal = checked_ord(year, month, day)?;
        let valid_time = time.hour < 24 && time.minute < 60 && time.second < 60;
        if !valid_time {
            return None;
        }
        let offset_us = match time.tz {
            None => None,
            Some((0, _)) => Some(0),
            Some((secs, us)) => {
                let total = secs * 1_000_000 + us;
                if total.abs() >= DAY_US {
                    return None;
                }
                Some(total)
            }
        };
        let day_us =
            ((time.hour * 60 + time.minute) * 60 + time.second) * 1_000_000 + time.microsecond;
        Some(Self {
            ordinal,
            day_us,
            offset_us,
        })
    }
}

fn at(b: &[u8], i: usize) -> u8 {
    b.get(i).copied().unwrap_or(0)
}

fn is_digit(c: u8) -> bool {
    c.is_ascii_digit()
}

fn find_separator(b: &[u8]) -> Option<usize> {
    let len = b.len();
    if len == 7 {
        return Some(7);
    }
    if b[4] == b'-' {
        if b[5] != b'W' {
            return Some(10);
        }
        if len > 8 && b[8] == b'-' {
            if len == 9 {
                return None;
            }
            return Some(if len > 10 && is_digit(b[10]) { 8 } else { 10 });
        }
        return Some(8);
    }
    if b[4] != b'W' {
        return Some(8);
    }
    let mut idx = 7;
    while idx < len && is_digit(b[idx]) {
        idx += 1;
    }
    Some(match idx {
        i if i < 9 => i,
        i if i % 2 == 0 => 7,
        _ => 8,
    })
}

/// `parse_digits`: reads `n` digits at `*pos`.
fn digits(b: &[u8], pos: &mut usize, n: usize) -> Option<i64> {
    let mut v = 0;
    for _ in 0..n {
        let c = at(b, *pos);
        *pos += 1;
        if !is_digit(c) {
            return None;
        }
        v = v * 10 + i64::from(c - b'0');
    }
    Some(v)
}

fn parse_date(b: &[u8], len: usize) -> Option<(i64, i64, i64)> {
    let mut p = 0;
    let year = digits(b, &mut p, 4)?;
    let uses_sep = at(b, p) == b'-';
    p += usize::from(uses_sep);
    if at(b, p) == b'W' {
        p += 1;
        let week = digits(b, &mut p, 2)?;
        let mut weekday = 1;
        if p < len {
            if uses_sep {
                let c = at(b, p);
                p += 1;
                if c != b'-' {
                    return None;
                }
            }
            weekday = digits(b, &mut p, 1)?;
        }
        return iso_to_ymd(year, week, weekday);
    }
    let month = digits(b, &mut p, 2)?;
    if uses_sep {
        let c = at(b, p);
        p += 1;
        if c != b'-' {
            return None;
        }
    }
    let day = digits(b, &mut p, 2)?;
    Some((year, month, day))
}

fn iso_to_ymd(year: i64, week: i64, day: i64) -> Option<(i64, i64, i64)> {
    if week <= 0 || week >= 53 {
        let first = ymd_to_ord(year, 1, 1) % 7;
        if !(week == 53 && (first == 4 || (first == 3 && is_leap(year)))) {
            return None;
        }
    }
    if day <= 0 || day >= 8 {
        return None;
    }
    let first_day = ymd_to_ord(year, 1, 1);
    let first_weekday = (first_day + 6) % 7;
    let mut monday = first_day - first_weekday;
    if first_weekday > 3 {
        monday += 7;
    }
    let ordinal = monday + (week - 1) * 7 + day - 1;
    // Outside 1..=9999 the datetime constructor raises ValueError.
    (1..=MAX_ORDINAL)
        .contains(&ordinal)
        .then(|| ord_to_ymd(ordinal))
}

#[derive(Default)]
struct Time {
    hour: i64,
    minute: i64,
    second: i64,
    microsecond: i64,
    /// (offset seconds, offset microseconds), each with the sign.
    tz: Option<(i64, i64)>,
}

/// `parse_hh_mm_ss_ff`: `Some(more)`, where `more` says that the text does
/// not end at the NUL after the parsed part. `None` is an error.
fn parse_hms(b: &[u8], start: usize, end: usize, vals: &mut [i64; 4]) -> Option<bool> {
    let mut p = start;
    let mut has_sep = true;
    for (i, val) in vals.iter_mut().take(3).enumerate() {
        *val = digits(b, &mut p, 2)?;
        let c = at(b, p);
        p += 1;
        if i == 0 {
            has_sep = c == b':';
        }
        if p >= end {
            return Some(c != 0);
        } else if has_sep && c == b':' {
            continue;
        } else if c == b'.' || c == b',' {
            break;
        } else if !has_sep {
            p -= 1;
        } else {
            return None;
        }
    }
    let remains = end.saturating_sub(p);
    let to_parse = remains.min(6);
    let mut us = digits(b, &mut p, to_parse)?;
    if to_parse < 6 {
        // C indexes correction[to_parse - 1]. to_parse is never 0: the loop
        // above returns when p reaches the end.
        us *= 10_i64.pow(6 - to_parse as u32);
    }
    vals[3] = us;
    while is_digit(at(b, p)) {
        p += 1;
    }
    Some(at(b, p) != 0)
}

fn parse_time(b: &[u8]) -> Option<Time> {
    let end = b.len();
    let mut tz_pos = 0;
    loop {
        let c = at(b, tz_pos);
        if c == b'Z' || c == b'+' || c == b'-' {
            break;
        }
        tz_pos += 1;
        if tz_pos >= end {
            break;
        }
    }
    let mut v = [0; 4];
    let more = parse_hms(b, 0, tz_pos, &mut v)?;
    let mut t = Time {
        hour: v[0],
        minute: v[1],
        second: v[2],
        microsecond: v[3],
        tz: None,
    };
    if tz_pos == end {
        return (!more).then_some(t);
    }
    if at(b, tz_pos) == b'Z' {
        t.tz = Some((0, 0));
        return (at(b, tz_pos + 1) == 0).then_some(t);
    }
    let sign = if at(b, tz_pos) == b'-' { -1 } else { 1 };
    let mut z = [0; 4];
    let more = parse_hms(b, tz_pos + 1, end, &mut z)?;
    if more {
        return None;
    }
    t.tz = Some((sign * (z[0] * 3600 + z[1] * 60 + z[2]), sign * z[3]));
    Some(t)
}
