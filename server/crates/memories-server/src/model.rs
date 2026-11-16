//! Request bodies that FastAPI checks with a pydantic model: the lax type
//! rules of pydantic 2 and its 422 error items, field by field in the order
//! of the model.

use serde_json::{Map, Value, json};

use crate::error::{item, missing, string_too_short};

/// The checks of one model at `loc` (for example `["body"]`).
pub struct Model<'a> {
    map: &'a Map<String, Value>,
    loc: Vec<Value>,
    pub errors: Vec<Value>,
}

/// Pydantic formats a float bound with `str()` of a number: `1.0` gives `1`.
fn bound_text(b: f64) -> String {
    if b.fract() == 0.0 && b.abs() < 1e16 {
        format!("{}", b as i64)
    } else {
        format!("{b}")
    }
}

/// Python `int(text)` for pydantic: white space, a sign, `_` between digits,
/// and a fraction of zeros (`"7.0"`).
pub fn parse_int_text(raw: &str) -> Option<i64> {
    let s = raw.trim();
    let (neg, s) = match s.as_bytes().first() {
        Some(b'-') => (true, &s[1..]),
        Some(b'+') => (false, &s[1..]),
        _ => (false, s),
    };
    let (int, frac) = s.split_once('.').unwrap_or((s, ""));
    if s.contains('.') && (frac.is_empty() || !frac.bytes().all(|b| b == b'0')) {
        return None;
    }
    let b = int.as_bytes();
    let digit_ok = |i: usize| {
        b[i].is_ascii_digit()
            || (b[i] == b'_'
                && i > 0
                && i + 1 < b.len()
                && b[i - 1].is_ascii_digit()
                && b[i + 1].is_ascii_digit())
    };
    if b.is_empty() || !(0..b.len()).all(digit_ok) {
        return None;
    }
    let digits: String = int.chars().filter(char::is_ascii_digit).collect();
    // Python ints have no size limit: a value past i64 saturates, so that
    // the `ge` or `le` check reports it as pydantic does.
    // shortcut: pydantic gives int_parsing_size past 4300 digits; this gives the bound error.
    Some(match digits.parse::<i64>() {
        Ok(v) if neg => -v,
        Ok(v) => v,
        Err(_) if neg => i64::MIN,
        Err(_) => i64::MAX,
    })
}

pub use memories_engine::records::py_float as parse_float_text;

/// Float bounds of a field: `ge`, `gt` and `le`.
#[derive(Clone, Copy, Default)]
pub struct Bounds {
    pub ge: Option<f64>,
    pub gt: Option<f64>,
    pub le: Option<f64>,
}

impl<'a> Model<'a> {
    pub fn new(map: &'a Map<String, Value>, loc: Vec<Value>) -> Self {
        Self {
            map,
            loc,
            errors: Vec::new(),
        }
    }

    fn at(&self, name: &str) -> Value {
        let mut l = self.loc.clone();
        l.push(json!(name));
        Value::Array(l)
    }

    /// The value of `name`. `Err(())` after a missing-field error.
    fn value(&mut self, name: &str, required: bool) -> Result<Option<&'a Value>, ()> {
        match self.map.get(name) {
            None if required => {
                let e = missing(self.at(name), Value::Object(self.map.clone()));
                self.errors.push(e);
                Err(())
            }
            v => Ok(v),
        }
    }

    fn push(&mut self, kind: &str, name: &str, msg: &str, input: &Value, ctx: Option<Value>) {
        let mut e = item(kind, self.at(name), msg, input.clone());
        if let Some(c) = ctx {
            e["ctx"] = c;
        }
        self.errors.push(e);
    }

    /// A `str` field with `min_length` and `max_length`. `default: None`
    /// means required. With `nullable`, null gives `None`.
    pub fn string(
        &mut self,
        name: &str,
        default: Option<&str>,
        nullable: bool,
        min: usize,
        max: usize,
    ) -> Option<String> {
        let v = match self.value(name, default.is_none()) {
            Err(()) => return None,
            Ok(None) => return default.map(str::to_owned),
            Ok(Some(Value::Null)) if nullable => return None,
            Ok(Some(v)) => v,
        };
        let Some(s) = v.as_str() else {
            self.push(
                "string_type",
                name,
                "Input should be a valid string",
                v,
                None,
            );
            return None;
        };
        let len = s.chars().count();
        if len < min {
            let e = string_too_short(self.at(name), v.clone(), min);
            self.errors.push(e);
        } else if len > max {
            let unit = if max == 1 { "character" } else { "characters" };
            let msg = format!("String should have at most {max} {unit}");
            self.push(
                "string_too_long",
                name,
                &msg,
                v,
                Some(json!({"max_length": max})),
            );
        } else {
            return Some(s.to_owned());
        }
        None
    }

    /// An `Optional[str] = None` field with `max_length`.
    pub fn opt_string(&mut self, name: &str, max: usize) -> Option<String> {
        match self.map.get(name) {
            None | Some(Value::Null) => None,
            Some(_) => self.string(name, Some(""), true, 0, max),
        }
    }

    /// An `int` field with `ge` and `le`.
    pub fn int(&mut self, name: &str, default: i64, ge: i64, le: i64) -> Option<i64> {
        let v = match self.value(name, false) {
            Err(()) | Ok(None) => return Some(default),
            Ok(Some(v)) => v,
        };
        let parsed = match v {
            Value::Bool(b) => Some(i64::from(*b)),
            Value::Number(n) => match (n.as_i64(), n.as_f64()) {
                (Some(i), _) => Some(i),
                // An integer past i64::MAX (serde keeps it as u64): the `le` check reports it.
                (None, _) if n.is_u64() => Some(i64::MAX),
                (None, Some(f)) if f.fract() != 0.0 => {
                    let msg =
                        "Input should be a valid integer, got a number with a fractional part";
                    self.push("int_from_float", name, msg, v, None);
                    return None;
                }
                (None, Some(f)) if f.abs() < 9.2e18 => Some(f as i64),
                _ => {
                    let msg = "Unable to parse input string as an integer, exceeded maximum size";
                    self.push("int_parsing_size", name, msg, v, None);
                    return None;
                }
            },
            Value::String(s) => match parse_int_text(s) {
                Some(i) => Some(i),
                None => {
                    let msg =
                        "Input should be a valid integer, unable to parse string as an integer";
                    self.push("int_parsing", name, msg, v, None);
                    return None;
                }
            },
            _ => None,
        };
        let Some(i) = parsed else {
            self.push("int_type", name, "Input should be a valid integer", v, None);
            return None;
        };
        if i < ge {
            let msg = format!("Input should be greater than or equal to {ge}");
            self.push("greater_than_equal", name, &msg, v, Some(json!({"ge": ge})));
            return None;
        }
        if i > le {
            let msg = format!("Input should be less than or equal to {le}");
            self.push("less_than_equal", name, &msg, v, Some(json!({"le": le})));
            return None;
        }
        Some(i)
    }

    /// A `float` field. `default: None` with `nullable` is `Optional[float] = None`.
    pub fn float(
        &mut self,
        name: &str,
        default: Option<f64>,
        nullable: bool,
        b: Bounds,
    ) -> Option<f64> {
        let v = match self.value(name, false) {
            Err(()) | Ok(None) => return default,
            Ok(Some(Value::Null)) if nullable => return None,
            Ok(Some(v)) => v,
        };
        let parsed = match v {
            Value::Bool(x) => Some(f64::from(u8::from(*x))),
            Value::Number(n) => n.as_f64(),
            Value::String(s) => match parse_float_text(s) {
                Some(f) => Some(f),
                None => {
                    let msg = "Input should be a valid number, unable to parse string as a number";
                    self.push("float_parsing", name, msg, v, None);
                    return None;
                }
            },
            _ => None,
        };
        let Some(f) = parsed else {
            self.push(
                "float_type",
                name,
                "Input should be a valid number",
                v,
                None,
            );
            return None;
        };
        let checks = [
            (
                b.gt,
                "greater_than",
                "greater than",
                "gt",
                (|f: f64, x: f64| f > x) as fn(f64, f64) -> bool,
            ),
            (
                b.ge,
                "greater_than_equal",
                "greater than or equal to",
                "ge",
                |f, x| f >= x,
            ),
            (
                b.le,
                "less_than_equal",
                "less than or equal to",
                "le",
                |f, x| f <= x,
            ),
        ];
        for (bound, kind, words, key, ok) in checks {
            if let Some(x) = bound
                && !ok(f, x)
            {
                let msg = format!("Input should be {words} {}", bound_text(x));
                self.push(kind, name, &msg, v, Some(json!({ key: x })));
                return None;
            }
        }
        Some(f)
    }

    /// A `bool` field.
    pub fn boolean(&mut self, name: &str, default: bool) -> Option<bool> {
        let v = match self.value(name, false) {
            Err(()) | Ok(None) => return Some(default),
            Ok(Some(v)) => v,
        };
        let parsing = "Input should be a valid boolean, unable to interpret input";
        match v {
            Value::Bool(b) => return Some(*b),
            Value::Number(n) => match (n.as_i64(), n.as_f64()) {
                (Some(0), _) => return Some(false),
                (Some(1), _) => return Some(true),
                (Some(_), _) => self.push("bool_parsing", name, parsing, v, None),
                (None, Some(f)) if f == 0.0 || f == 1.0 => return Some(f == 1.0),
                _ => self.push(
                    "bool_type",
                    name,
                    "Input should be a valid boolean",
                    v,
                    None,
                ),
            },
            Value::String(s) => match s.to_lowercase().as_str() {
                "0" | "off" | "f" | "false" | "n" | "no" => return Some(false),
                "1" | "on" | "t" | "true" | "y" | "yes" => return Some(true),
                _ => self.push("bool_parsing", name, parsing, v, None),
            },
            _ => self.push(
                "bool_type",
                name,
                "Input should be a valid boolean",
                v,
                None,
            ),
        }
        None
    }

    /// An `Optional[List[str]] = None` field.
    pub fn string_list(&mut self, name: &str) -> Option<Vec<String>> {
        let v = match self.value(name, false) {
            Err(()) | Ok(None) | Ok(Some(Value::Null)) => return None,
            Ok(Some(v)) => v,
        };
        let Some(items) = v.as_array() else {
            self.push("list_type", name, "Input should be a valid list", v, None);
            return None;
        };
        let mut out = Vec::with_capacity(items.len());
        let before = self.errors.len();
        for (i, x) in items.iter().enumerate() {
            match x.as_str() {
                Some(s) => out.push(s.to_owned()),
                None => {
                    let mut l = self.loc.clone();
                    l.extend([json!(name), json!(i)]);
                    self.errors.push(item(
                        "string_type",
                        Value::Array(l),
                        "Input should be a valid string",
                        x.clone(),
                    ));
                }
            }
        }
        (self.errors.len() == before).then_some(out)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn number_texts_follow_python() {
        assert_eq!(parse_int_text(" 7 "), Some(7));
        assert_eq!(parse_int_text("1_0"), Some(10));
        assert_eq!(parse_int_text("7.00"), Some(7));
        assert_eq!(parse_int_text("1e2"), None);
        assert_eq!(parse_float_text(" 1e-2 "), Some(0.01));
        assert_eq!(parse_float_text("0x1"), None);
        assert_eq!(bound_text(365.0), "365");
        assert_eq!(bound_text(0.5), "0.5");
    }

    #[test]
    fn integers_past_i64_fail_the_bound_check() {
        let types = |body: Value| {
            let map = body.as_object().unwrap().clone();
            let mut m = Model::new(&map, vec![json!("body")]);
            assert_eq!(m.int("k", 5, 1, 100), None);
            m.errors[0]["type"].as_str().unwrap().to_owned()
        };
        assert_eq!(
            types(json!({"k": 9223372036854775808u64})),
            "less_than_equal"
        );
        assert_eq!(
            types(json!({"k": "100000000000000000000"})),
            "less_than_equal"
        );
        assert_eq!(
            types(json!({"k": "-100000000000000000000"})),
            "greater_than_equal"
        );
    }
}
