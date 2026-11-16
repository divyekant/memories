//! The 422 bodies and parsed values of the search request models, against
//! `parity/golden/search/validation.ndjson` (pydantic of the Python server).

use memories_server::search::{SearchBody, parse_batch, parse_is_novel};
use serde_json::{Value, json};

fn body_json(b: &SearchBody) -> Value {
    json!({
        "query": b.query, "k": b.k, "threshold": b.threshold, "hybrid": b.hybrid,
        "vector_weight": b.vector_weight, "source_prefix": b.source_prefix,
        "source_prefixes": b.source_prefixes, "source_boundary": b.source_boundary,
        "recency_weight": b.recency_weight, "recency_half_life_days": b.recency_half_life_days,
        "feedback_weight": b.feedback_weight, "confidence_weight": b.confidence_weight,
        "graph_weight": b.graph_weight, "since": b.since, "until": b.until, "source": b.source,
        "include_archived": b.include_archived, "auto_intent": b.auto_intent,
        "reference_date": b.reference_date,
    })
}

/// Python gives an int for a float field that the body gave as an int.
fn same(a: &Value, b: &Value) -> bool {
    match (a, b) {
        (Value::Number(x), Value::Number(y)) => x.as_f64() == y.as_f64(),
        (Value::Object(x), Value::Object(y)) => {
            x.len() == y.len() && x.iter().all(|(k, v)| y.get(k).is_some_and(|w| same(v, w)))
        }
        (Value::Array(x), Value::Array(y)) => {
            x.len() == y.len() && x.iter().zip(y).all(|(v, w)| same(v, w))
        }
        _ => a == b,
    }
}

#[test]
fn validation_matches_pydantic() {
    let path = concat!(
        env!("CARGO_MANIFEST_DIR"),
        "/../../../parity/golden/search/validation.ndjson"
    );
    let text = std::fs::read_to_string(path).expect("golden file");
    let mut n = 0;
    for line in text.lines() {
        let c: Value = serde_json::from_str(line).unwrap();
        let map = c["body"].as_object().unwrap();
        let (detail, value) = match c["model"].as_str().unwrap() {
            "search" => match SearchBody::from_map(map) {
                Ok(b) => (Value::Null, body_json(&b)),
                Err(e) => (e.detail, Value::Null),
            },
            "batch" => match parse_batch(map) {
                Ok(bs) => (
                    Value::Null,
                    json!({"queries": bs.iter().map(body_json).collect::<Vec<_>>()}),
                ),
                Err(e) => (e.detail, Value::Null),
            },
            _ => match parse_is_novel(map) {
                Ok((t, th)) => (Value::Null, json!({"text": t, "threshold": th})),
                Err(e) => (e.detail, Value::Null),
            },
        };
        // The 422 items must be equal, number types included.
        assert_eq!(detail, c["detail"], "body {}", c["body"]);
        assert!(
            same(&value, &c["value"]),
            "body {}: {value} != {}",
            c["body"],
            c["value"]
        );
        n += 1;
    }
    assert!(n > 60);
}
