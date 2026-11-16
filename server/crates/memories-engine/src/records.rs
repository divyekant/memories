//! A stored record as the server reads it: `MemoryEngine._enrich_with_confidence`
//! of v5.19.0 adds `confidence`, calculated at each read.

use memories_types::Record;
use serde_json::Value;

use crate::rank::{PyDateTime, RecordFields};

/// Python gives a truthy value that is not a string to `fromisoformat`,
/// which fails. A string that does not parse gives the same result.
const NOT_A_DATE: &str = "not a date";

fn truthy(v: &Value) -> bool {
    match v {
        Value::Null => false,
        Value::Bool(b) => *b,
        Value::Number(n) => n.as_f64() != Some(0.0),
        Value::String(s) => !s.is_empty(),
        Value::Array(a) => !a.is_empty(),
        Value::Object(o) => !o.is_empty(),
    }
}

/// A typed date field, or a stand-in for a truthy value of another type.
fn date<'a>(typed: &'a Option<String>, record: &Record, key: &str) -> Option<&'a str> {
    match typed {
        Some(s) => Some(s),
        None => record
            .extra
            .get(key)
            .filter(|v| truthy(v))
            .map(|_| NOT_A_DATE),
    }
}

/// The fields of `record` that the rank rules read.
pub fn rank_fields(record: &Record, confidence_half_life_days: Option<f64>) -> RecordFields<'_> {
    RecordFields {
        document_at: date(&record.document_at, record, "document_at"),
        created_at: date(&record.created_at, record, "created_at"),
        timestamp: date(&record.timestamp, record, "timestamp"),
        updated_at: date(&record.updated_at, record, "updated_at"),
        last_reinforced_at: date(&record.last_reinforced_at, record, "last_reinforced_at"),
        confidence_half_life_days,
    }
}

/// The record as a read returns it: all stored fields plus `confidence`.
/// `confidence_half_life_days` comes from the extraction profile of the source.
pub fn with_confidence(
    record: &Record,
    confidence_half_life_days: Option<f64>,
    now: &PyDateTime,
) -> Value {
    let confidence = rank_fields(record, confidence_half_life_days).confidence(now);
    let mut value = record.to_json();
    value["confidence"] = confidence.into();
    value
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn now() -> PyDateTime {
        PyDateTime::fromisoformat("2026-11-16T00:00:00+00:00").unwrap()
    }

    fn confidence(value: Value, half_life: Option<f64>) -> f64 {
        let r = Record::from_json(value).unwrap();
        with_confidence(&r, half_life, &now())["confidence"]
            .as_f64()
            .unwrap()
    }

    // Expected values: MemoryEngine.compute_confidence at the same `now`, with
    // round(_, 4).
    #[test]
    fn confidence_follows_python() {
        let base = json!({"id": 1, "created_at": "2026-08-18T00:00:00+00:00"});
        assert_eq!(confidence(base.clone(), None), 0.5);
        assert_eq!(confidence(base, Some(45.0)), 0.25);
        // last_reinforced_at comes first; an empty string falls through.
        assert_eq!(
            confidence(
                json!({"id": 1, "last_reinforced_at": "2026-11-16T00:00:00",
                              "updated_at": "2020-01-01T00:00:00"}),
                None
            ),
            1.0
        );
        assert_eq!(
            confidence(
                json!({"id": 1, "last_reinforced_at": "",
                              "updated_at": "2026-08-18T00:00:00+00:00"}),
                None
            ),
            0.5
        );
        assert_eq!(confidence(json!({"id": 1}), None), 0.0);
        assert_eq!(
            confidence(json!({"id": 1, "created_at": "2027-01-01"}), None),
            1.0
        );
        assert_eq!(confidence(json!({"id": 1, "created_at": "bad"}), None), 0.0);
        // A truthy value that is not a string stops the chain with 0.0;
        // a falsy one falls through.
        assert_eq!(
            confidence(
                json!({"id": 1, "updated_at": 5, "created_at": "2026-11-16T00:00:00"}),
                None
            ),
            0.0
        );
        assert_eq!(
            confidence(
                json!({"id": 1, "updated_at": 0, "created_at": "2026-11-16T00:00:00"}),
                None
            ),
            1.0
        );
    }

    #[test]
    fn the_read_keeps_each_stored_field() {
        let value = json!({"id": 3, "text": "t", "source": "s", "x": [1, null],
                           "created_at": "2026-11-16T00:00:00+00:00"});
        let r = Record::from_json(value.clone()).unwrap();
        let mut want = value;
        want["confidence"] = json!(1.0);
        assert_eq!(with_confidence(&r, None, &now()), want);
    }
}
