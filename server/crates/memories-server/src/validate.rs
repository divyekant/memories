//! Body checks of the pydantic 2 models of the write routes, in lax mode as
//! FastAPI runs them. Each error item has the pydantic type, message and `loc`.

use serde_json::{Map, Value, json};

use crate::error::{ApiError, int_parsing, item, missing, py_int, string_too_short};

/// The fields of one JSON object at `loc` (`["body"]` or `["body", "memories", 0]`).
pub struct Fields<'a> {
    map: &'a Map<String, Value>,
    loc: Vec<Value>,
    errors: &'a mut Vec<Value>,
}

fn with_ctx(mut v: Value, ctx: Value) -> Value {
    v["ctx"] = ctx;
    v
}

impl<'a> Fields<'a> {
    pub fn new(map: &'a Map<String, Value>, loc: Vec<Value>, errors: &'a mut Vec<Value>) -> Self {
        Self { map, loc, errors }
    }

    fn at(&self, name: &str) -> Value {
        let mut l = self.loc.clone();
        l.push(json!(name));
        Value::Array(l)
    }

    /// The value of an optional field: `None` if it is absent or null.
    fn optional(&self, name: &str) -> Option<&'a Value> {
        self.map.get(name).filter(|v| !v.is_null())
    }

    /// The value of a required field. An absent field is an error.
    fn required(&mut self, name: &str) -> Option<&'a Value> {
        let v = self.map.get(name);
        if v.is_none() {
            let e = missing(self.at(name), Value::Object(self.map.clone()));
            self.errors.push(e);
        }
        v
    }

    /// `str = Field(min_length, max_length, pattern)`.
    pub fn string(
        &mut self,
        name: &str,
        required: bool,
        (min, max): (usize, usize),
        pattern: Option<(&str, fn(&str) -> bool)>,
    ) -> Option<String> {
        let v = if required {
            self.required(name)?
        } else {
            self.optional(name)?
        };
        let loc = self.at(name);
        let Some(s) = v.as_str() else {
            let e = item("string_type", loc, "Input should be a valid string", v.clone());
            self.errors.push(e);
            return None;
        };
        let len = s.chars().count();
        let e = if len < min {
            string_too_short(loc, v.clone(), min)
        } else if len > max {
            let msg = format!("String should have at most {max} characters");
            with_ctx(
                item("string_too_long", loc, &msg, v.clone()),
                json!({"max_length": max}),
            )
        } else if let Some((p, _)) = pattern.filter(|(_, ok)| !ok(s)) {
            let msg = format!("String should match pattern '{p}'");
            with_ctx(
                item("string_pattern_mismatch", loc, &msg, v.clone()),
                json!({"pattern": p}),
            )
        } else {
            return Some(s.to_owned());
        };
        self.errors.push(e);
        None
    }

    /// `Optional[dict] = None`.
    pub fn dict(&mut self, name: &str) -> Option<Map<String, Value>> {
        let v = self.optional(name)?;
        if let Some(m) = v.as_object() {
            return Some(m.clone());
        }
        let e = item(
            "dict_type",
            self.at(name),
            "Input should be a valid dictionary",
            v.clone(),
        );
        self.errors.push(e);
        None
    }

    /// `bool = default`, or `Optional[bool] = None` when `default` is `None`.
    pub fn boolean(&mut self, name: &str, default: Option<bool>) -> Option<bool> {
        let v = match self.map.get(name) {
            None => return default,
            Some(Value::Null) if default.is_none() => return None,
            Some(v) => v,
        };
        let parsed = match v {
            Value::Bool(b) => Ok(*b),
            Value::Number(n) => match (n.as_i64(), n.as_f64()) {
                (Some(0), _) => Ok(false),
                (Some(1), _) => Ok(true),
                (Some(_), _) => Err("bool_parsing"),
                (None, Some(f)) if f == 0.0 => Ok(false),
                (None, Some(f)) if f == 1.0 => Ok(true),
                _ => Err("bool_type"),
            },
            Value::String(s) => match s.to_lowercase().as_str() {
                "0" | "off" | "f" | "false" | "n" | "no" => Ok(false),
                "1" | "on" | "t" | "true" | "y" | "yes" => Ok(true),
                _ => Err("bool_parsing"),
            },
            _ => Err("bool_type"),
        };
        match parsed {
            Ok(b) => Some(b),
            Err(kind) => {
                let msg = if kind == "bool_parsing" {
                    "Input should be a valid boolean, unable to interpret input"
                } else {
                    "Input should be a valid boolean"
                };
                let e = item(kind, self.at(name), msg, v.clone());
                self.errors.push(e);
                default
            }
        }
    }

    /// A required list with `min_length` and `max_length`. `each` checks one
    /// item at its `loc`. The length check runs only when each item is valid.
    pub fn list<T>(
        &mut self,
        name: &str,
        (min, max): (usize, usize),
        mut each: impl FnMut(&Value, Vec<Value>, &mut Vec<Value>) -> Option<T>,
    ) -> Option<Vec<T>> {
        let v = self.required(name)?;
        let loc = self.at(name);
        let Some(items) = v.as_array() else {
            let e = item("list_type", loc, "Input should be a valid list", v.clone());
            self.errors.push(e);
            return None;
        };
        let before = self.errors.len();
        let mut out = Vec::with_capacity(items.len());
        for (i, x) in items.iter().enumerate() {
            let mut l = loc.as_array().unwrap().clone();
            l.push(json!(i));
            if let Some(t) = each(x, l, self.errors) {
                out.push(t);
            }
        }
        if self.errors.len() > before {
            return None;
        }
        let n = items.len();
        let e = if n < min {
            crate::error::list_too_short(loc, v.clone(), min, n)
        } else if n > max {
            let msg = format!("List should have at most {max} items after validation, not {n}");
            with_ctx(
                item("too_long", loc, &msg, v.clone()),
                json!({"field_type": "List", "max_length": max, "actual_length": n}),
            )
        } else {
            return Some(out);
        };
        self.errors.push(e);
        None
    }
}

/// One item of a list of models: `model_type` if it is not an object.
pub fn model<'a>(
    v: &'a Value,
    loc: Vec<Value>,
    errors: &mut Vec<Value>,
    class: &str,
) -> Option<&'a Map<String, Value>> {
    let m = v.as_object();
    if m.is_none() {
        let msg = format!("Input should be a valid dictionary or instance of {class}");
        let e = item("model_type", Value::Array(loc), &msg, v.clone());
        errors.push(with_ctx(e, json!({"class_name": class})));
    }
    m
}

/// One `int` item in lax mode. A value above `i64::MAX` saturates; it is not
/// a stored ID, so the result for the caller does not change.
pub fn int(v: &Value, loc: Vec<Value>, errors: &mut Vec<Value>) -> Option<i64> {
    let loc = Value::Array(loc);
    let r = match v {
        Value::Bool(b) => Some(i64::from(*b)),
        Value::Number(n) => {
            if let Some(i) = n.as_i64() {
                Some(i)
            } else if n.as_u64().is_some() {
                Some(i64::MAX)
            } else {
                let f = n.as_f64().unwrap_or(f64::NAN);
                if f.fract() == 0.0 {
                    Some(f as i64)
                } else {
                    errors.push(item(
                        "int_from_float",
                        loc,
                        "Input should be a valid integer, got a number with a fractional part",
                        v.clone(),
                    ));
                    return None;
                }
            }
        }
        Value::String(s) => {
            let r = py_int(s);
            if r.is_none() {
                errors.push(int_parsing(loc, v.clone()));
            }
            return r;
        }
        _ => None,
    };
    if r.is_none() {
        errors.push(item("int_type", loc, "Input should be a valid integer", v.clone()));
    }
    r
}

/// `Ok` if no check failed, else the 422 reply with all error items.
pub fn finish<T>(errors: Vec<Value>, value: Option<T>) -> Result<T, ApiError> {
    match value {
        Some(v) if errors.is_empty() => Ok(v),
        _ => Err(ApiError::validation(errors)),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn obj(v: Value) -> Map<String, Value> {
        v.as_object().unwrap().clone()
    }

    fn add_request(m: &Map<String, Value>, loc: Vec<Value>, errors: &mut Vec<Value>) {
        let mut f = Fields::new(m, loc, errors);
        f.string("text", true, (1, 50000), None);
        f.string("source", true, (1, 500), None);
        f.dict("metadata");
        f.boolean("deduplicate", Some(false));
        f.string(
            "on_duplicate",
            false,
            (0, usize::MAX),
            Some(("^(add|skip|supersede)$", |s| {
                matches!(s, "add" | "skip" | "supersede")
            })),
        );
    }

    fn errors_of(body: Value) -> Value {
        let m = obj(body);
        let mut errors = Vec::new();
        add_request(&m, vec![json!("body")], &mut errors);
        Value::Array(errors)
    }

    // Expected items: pydantic 2 of v5.19.0, with FastAPI's "body" prefix.
    #[test]
    fn add_request_errors() {
        assert_eq!(
            errors_of(json!({"text": "x", "source": "s", "on_duplicate": "bad",
                             "deduplicate": "maybe", "metadata": [1]})),
            json!([
                {"type": "dict_type", "loc": ["body", "metadata"], "msg": "Input should be a valid dictionary", "input": [1]},
                {"type": "bool_parsing", "loc": ["body", "deduplicate"], "msg": "Input should be a valid boolean, unable to interpret input", "input": "maybe"},
                {"type": "string_pattern_mismatch", "loc": ["body", "on_duplicate"], "msg": "String should match pattern '^(add|skip|supersede)$'", "input": "bad", "ctx": {"pattern": "^(add|skip|supersede)$"}}
            ])
        );
        assert_eq!(
            errors_of(json!({"text": 5, "source": "s", "on_duplicate": 5})),
            json!([
                {"type": "string_type", "loc": ["body", "text"], "msg": "Input should be a valid string", "input": 5},
                {"type": "string_type", "loc": ["body", "on_duplicate"], "msg": "Input should be a valid string", "input": 5}
            ])
        );
        let long = "s".repeat(501);
        assert_eq!(
            errors_of(json!({"text": "x", "source": long}))[0],
            json!({"type": "string_too_long", "loc": ["body", "source"], "msg": "String should have at most 500 characters", "input": long, "ctx": {"max_length": 500}})
        );
        assert_eq!(
            errors_of(json!({"source": "x", "text": ""})),
            json!([{"type": "string_too_short", "loc": ["body", "text"], "msg": "String should have at least 1 character", "input": "", "ctx": {"min_length": 1}}])
        );
    }

    #[test]
    fn lax_booleans() {
        for (v, ok) in [
            (json!("TRUE"), true),
            (json!("Yes"), true),
            (json!(1), true),
            (json!(1.0), true),
            (json!(0.0), true),
            (json!(" true"), false),
            (json!("2"), false),
            (json!(2), false),
            (json!(0.5), false),
            (Value::Null, false),
            (json!([]), false),
        ] {
            let e = errors_of(json!({"text": "x", "source": "s", "deduplicate": v}));
            assert_eq!(e.as_array().unwrap().is_empty(), ok, "{v}: {e}");
        }
        let kind = |v: Value| errors_of(json!({"text": "x", "source": "s", "deduplicate": v}))[0]["type"].clone();
        assert_eq!(kind(json!(2)), "bool_parsing");
        assert_eq!(kind(json!(0.5)), "bool_type");
        assert_eq!(kind(Value::Null), "bool_type");
    }

    #[test]
    fn batch_items_and_lengths() {
        let m = obj(json!({"memories": [{"text": ""}, 5], "deduplicate": 2}));
        let mut errors = Vec::new();
        let mut f = Fields::new(&m, vec![json!("body")], &mut errors);
        f.list("memories", (1, 500), |v, loc, errs| {
            let m = model(v, loc.clone(), errs, "AddMemoryRequest")?;
            add_request(m, loc, errs);
            Some(())
        });
        f.boolean("deduplicate", Some(false));
        assert_eq!(
            Value::Array(errors),
            json!([
                {"type": "string_too_short", "loc": ["body", "memories", 0, "text"], "msg": "String should have at least 1 character", "input": "", "ctx": {"min_length": 1}},
                {"type": "missing", "loc": ["body", "memories", 0, "source"], "msg": "Field required", "input": {"text": ""}},
                {"type": "model_type", "loc": ["body", "memories", 1], "msg": "Input should be a valid dictionary or instance of AddMemoryRequest", "input": 5, "ctx": {"class_name": "AddMemoryRequest"}},
                {"type": "bool_parsing", "loc": ["body", "deduplicate"], "msg": "Input should be a valid boolean, unable to interpret input", "input": 2}
            ])
        );
        let check = |body: Value| {
            let m = obj(body);
            let mut errors = Vec::new();
            Fields::new(&m, vec![json!("body")], &mut errors).list("ids", (1, 2), |v, l, e| int(v, l, e));
            Value::Array(errors)
        };
        assert_eq!(
            check(json!({"ids": []}))[0],
            json!({"type": "too_short", "loc": ["body", "ids"], "msg": "List should have at least 1 item after validation, not 0", "input": [], "ctx": {"field_type": "List", "min_length": 1, "actual_length": 0}})
        );
        assert_eq!(
            check(json!({"ids": [1, 2, 3]}))[0],
            json!({"type": "too_long", "loc": ["body", "ids"], "msg": "List should have at most 2 items after validation, not 3", "input": [1, 2, 3], "ctx": {"field_type": "List", "max_length": 2, "actual_length": 3}})
        );
        assert_eq!(
            check(json!({"ids": "x"}))[0],
            json!({"type": "list_type", "loc": ["body", "ids"], "msg": "Input should be a valid list", "input": "x"})
        );
    }

    #[test]
    fn lax_ints() {
        let one = |v: Value| {
            let mut e = Vec::new();
            let r = int(&v, vec![json!("body"), json!("ids"), json!(0)], &mut e);
            (r, e.first().map(|x| x["type"].clone()))
        };
        for (v, want) in [
            (json!(" 7 "), 7),
            (json!("1_0"), 10),
            (json!("+3"), 3),
            (json!("-2"), -2),
            (json!("7.0"), 7),
            (json!(true), 1),
            (json!(7.0), 7),
        ] {
            assert_eq!(one(v.clone()).0, Some(want), "{v}");
        }
        assert_eq!(one(json!("7.5")).1, Some(json!("int_parsing")));
        assert_eq!(one(json!("")).1, Some(json!("int_parsing")));
        assert_eq!(one(json!(7.5)).1, Some(json!("int_from_float")));
        assert_eq!(one(Value::Null).1, Some(json!("int_type")));
        assert_eq!(one(json!({})).1, Some(json!("int_type")));
    }
}
