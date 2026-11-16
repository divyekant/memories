//! Golden cases of the rank core: `parity/golden/rank/*.json`, which
//! `python -m parity.rank_golden` writes from the Python server v5.19.0.

use std::collections::{HashMap, HashSet};
use std::path::PathBuf;

use memories_engine::rank::fusion::{HybridInput, RecordFields, hybrid_rank};
use memories_engine::rank::graph::build_adjacency;
use memories_engine::rank::intent::classify_query;
use memories_engine::rank::pydt::PyDateTime;
use memories_engine::rank::pyorder::PySet;
use memories_engine::rank::scores::{
    WeightInput, Weights, compute_confidence, doc_tokens, first_set, passes_temporal_filter,
    py_round, query_tokens, recency_score, relative_scores,
};
use serde_json::Value;

fn cases(name: &str) -> Vec<Value> {
    let path = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("../../../parity/golden/rank")
        .join(format!("{name}.json"));
    let text = std::fs::read_to_string(&path).unwrap_or_else(|e| panic!("{path:?}: {e}"));
    let doc: Value = serde_json::from_str(&text).expect("golden json");
    let cases = doc["cases"].as_array().expect("cases").clone();
    assert!(!cases.is_empty(), "{name}: no cases");
    cases
}

fn f(v: &Value) -> f64 {
    v.as_f64().unwrap_or_else(|| panic!("not a number: {v}"))
}

fn opt_f(v: &Value) -> Option<f64> {
    v.as_f64()
}

fn s(v: &Value) -> Option<&str> {
    v.as_str()
}

fn ids(v: &Value) -> Vec<i64> {
    v.as_array()
        .unwrap()
        .iter()
        .map(|x| x.as_i64().unwrap())
        .collect()
}

fn pairs(v: &Value) -> Vec<(i64, f64)> {
    v.as_array()
        .map(|a| {
            a.iter()
                .map(|p| (p[0].as_i64().unwrap(), f(&p[1])))
                .collect()
        })
        .unwrap_or_default()
}

fn now(v: &Value) -> PyDateTime {
    PyDateTime::fromisoformat(v.as_str().unwrap()).unwrap()
}

/// Fails with the count and the first differences.
fn report(name: &str, total: usize, failures: Vec<String>) {
    assert!(
        failures.is_empty(),
        "{name}: {} of {total} cases differ:\n{}",
        failures.len(),
        failures
            .iter()
            .take(8)
            .cloned()
            .collect::<Vec<_>>()
            .join("\n")
    );
}

#[test]
fn tokens() {
    let cases = cases("tokens");
    let mut bad = Vec::new();
    for c in &cases {
        let input = c["input"].as_str().unwrap();
        let want_q: Vec<String> = serde_json::from_value(c["query_tokens"].clone()).unwrap();
        let want_d: Vec<String> = serde_json::from_value(c["doc_tokens"].clone()).unwrap();
        if query_tokens(input) != want_q || doc_tokens(input) != want_d {
            bad.push(format!(
                "{input:?}: {:?} {:?}",
                query_tokens(input),
                doc_tokens(input)
            ));
        }
    }
    report("tokens", cases.len(), bad);
}

#[test]
fn isoformat() {
    let cases = cases("isoformat");
    let mut bad = Vec::new();
    for c in &cases {
        let input = c["input"].as_str().unwrap();
        let got = PyDateTime::fromisoformat(input);
        let want = &c["result"];
        let same = match (&got, want.is_null()) {
            (None, true) => true,
            (Some(dt), false) => {
                ids(&want["fields"]) == dt.fields() && want["offset_us"].as_i64() == dt.offset_us
            }
            _ => false,
        };
        if !same {
            bad.push(format!("{input:?}: got {got:?}, want {want}"));
        }
    }
    report("isoformat", cases.len(), bad);
}

#[test]
fn round() {
    let cases = cases("round");
    let mut bad = Vec::new();
    for c in &cases {
        let (x, n, want) = (
            f(&c["x"]),
            c["n"].as_u64().unwrap() as usize,
            f(&c["result"]),
        );
        let got = py_round(x, n);
        if got.to_bits() != want.to_bits() {
            bad.push(format!("round({x:?}, {n}) = {got:?}, want {want:?}"));
        }
    }
    report("round", cases.len(), bad);
}

#[test]
fn recency_and_confidence() {
    for (name, func) in [
        (
            "recency",
            recency_score as fn(Option<&str>, f64, &PyDateTime) -> f64,
        ),
        ("confidence", compute_confidence),
    ] {
        let cases = cases(name);
        let reference = now(&Value::from("2026-06-15T12:34:56.789012+00:00"));
        let mut bad = Vec::new();
        for c in &cases {
            let got = func(s(&c["ts"]), f(&c["half_life"]), &reference);
            let want = f(&c["result"]);
            if got.to_bits() != want.to_bits() {
                bad.push(format!("{}: {got:?}, want {want:?}", c));
            }
        }
        report(name, cases.len(), bad);
    }
}

#[test]
fn temporal_filter() {
    let cases = cases("temporal");
    let mut bad = Vec::new();
    for c in &cases {
        let m = &c["meta"];
        let doc = first_set(&[
            s(&m["document_at"]),
            s(&m["created_at"]),
            s(&m["timestamp"]),
        ]);
        let got = passes_temporal_filter(doc, s(&c["since"]), s(&c["until"]));
        if got != c["result"].as_bool().unwrap() {
            bad.push(c.to_string());
        }
    }
    report("temporal", cases.len(), bad);
}

#[test]
fn relative_score() {
    let cases = cases("relative");
    let mut bad = Vec::new();
    for c in &cases {
        let raw: Vec<f64> = c["input"]
            .as_array()
            .unwrap()
            .iter()
            .map(|r| {
                opt_f(&r["similarity"])
                    .or_else(|| opt_f(&r["rrf_score"]))
                    .unwrap_or(0.0)
            })
            .collect();
        let want: Vec<f64> = c["result"].as_array().unwrap().iter().map(f).collect();
        if relative_scores(&raw) != want {
            bad.push(format!("{c}: got {:?}", relative_scores(&raw)));
        }
    }
    report("relative", cases.len(), bad);
}

#[test]
fn query_intent() {
    let cases = cases("intent");
    let mut bad = Vec::new();
    for c in &cases {
        let reference = PyDateTime::fromisoformat(c["reference"].as_str().unwrap()).unwrap();
        let [y, m, d, ..] = reference.fields();
        let got = classify_query(
            c["query"].as_str().unwrap(),
            (y, m, d),
            s(&c["caller_since"]),
            s(&c["caller_until"]),
            opt_f(&c["caller_graph_weight"]),
        );
        let want = &c["result"];
        let same = match &got {
            Err(_) => want.get("error").is_some(),
            Ok(a) => {
                want.get("error").is_none()
                    && a.since.as_deref() == s(&want["since"])
                    && a.until.as_deref() == s(&want["until"])
                    && a.graph_weight == opt_f(&want["graph_weight"])
                    && a.recency_weight == opt_f(&want["recency_weight"])
                    && Some(a.auto_detected) == want["auto_detected"].as_bool()
            }
        };
        if !same {
            bad.push(format!("{c}: got {got:?}"));
        }
    }
    report("intent", cases.len(), bad);
}

#[test]
fn python_set_order() {
    let cases = cases("pyset");
    let mut bad = Vec::new();
    for c in &cases {
        let a: PySet = ids(&c["adds"]).into_iter().collect();
        let b: PySet = ids(&c["other"]).into_iter().collect();
        let order: Vec<i64> = a.iter().collect();
        let and_order: Vec<i64> = a.intersection(&b).iter().collect();
        if order != ids(&c["order"]) || and_order != ids(&c["and_order"]) {
            bad.push(format!("adds {:?}", ids(&c["adds"])));
        }
    }
    report("pyset", cases.len(), bad);
}

struct Record {
    fields: [Option<String>; 5],
    half_life: Option<f64>,
}

fn hybrid_case(c: &Value) -> Result<(), String> {
    let metas = c["metas"].as_array().unwrap();
    let vector = pairs(&c["vector_hits"]);
    let keyword = pairs(&c["keyword_hits"]);
    let feedback = c["feedback"].as_array().map(|_| pairs(&c["feedback"]));
    let now_text = c["now"].as_str().unwrap();
    let profiles: Vec<(String, f64)> = c["profiles"]
        .as_array()
        .map(|a| {
            a.iter()
                .map(|p| (p[0].as_str().unwrap().to_owned(), f(&p[1])))
                .collect()
        })
        .unwrap_or_default();
    let vector_ids: HashSet<i64> = vector.iter().map(|v| v.0).collect();

    let mut records: HashMap<i64, Record> = HashMap::new();
    for m in metas {
        let id = m["id"].as_i64().unwrap();
        let get = |k: &str| m[k].as_str().map(str::to_owned);
        let mut fields = [
            get("document_at"),
            get("created_at"),
            get("timestamp"),
            get("updated_at"),
            get("last_reinforced_at"),
        ];
        // /search: the vector search reinforces its hits before the fusion.
        if vector_ids.contains(&id) {
            fields[4] = Some(now_text.to_owned());
        }
        let source = m["source"].as_str().unwrap_or("");
        let half_life = profiles
            .iter()
            .find(|p| source.starts_with(p.0.as_str()))
            .map(|p| p.1);
        records.insert(id, Record { fields, half_life });
    }
    let record = |id: i64| {
        records.get(&id).map(|r| RecordFields {
            document_at: r.fields[0].as_deref(),
            created_at: r.fields[1].as_deref(),
            timestamp: r.fields[2].as_deref(),
            updated_at: r.fields[3].as_deref(),
            last_reinforced_at: r.fields[4].as_deref(),
            confidence_half_life_days: r.half_life,
        })
    };
    let links: Vec<(i64, i64, String)> = metas
        .iter()
        .flat_map(|m| {
            let from = m["id"].as_i64().unwrap();
            m["links"]
                .as_array()
                .cloned()
                .unwrap_or_default()
                .into_iter()
                .map(move |l| {
                    (
                        from,
                        l["to_id"].as_i64().unwrap(),
                        l["type"].as_str().unwrap().to_owned(),
                    )
                })
        })
        .collect();
    let adjacency = build_adjacency(links.iter().map(|(a, b, t)| (*a, *b, t.as_str())), |id| {
        records.contains_key(&id)
    });
    let visible_set: HashSet<i64> = ids(&c["visible"]).into_iter().collect();
    let visible = |id: i64| visible_set.contains(&id);
    let p = &c["params"];
    let input = HybridInput {
        k: p["k"].as_u64().unwrap() as usize,
        record_count: metas.len(),
        threshold: opt_f(&p["threshold"]),
        weights: Weights::new(WeightInput {
            vector: f(&p["vector_weight"]),
            recency: f(&p["recency_weight"]),
            recency_half_life_days: f(&p["recency_half_life_days"]),
            feedback: f(&p["feedback_weight"]),
            confidence: f(&p["confidence_weight"]),
            graph: f(&p["graph_weight"]),
        }),
        since: s(&p["since"]),
        until: s(&p["until"]),
        vector: &vector,
        keyword: &keyword,
        feedback: feedback.as_deref(),
        record: &record,
        visible: if c["filtered"].as_bool().unwrap() {
            Some(&visible)
        } else {
            None
        },
        adjacency: &adjacency,
        now: PyDateTime::fromisoformat(now_text).unwrap(),
    };
    let got = hybrid_rank(&input);

    let want = &c["expected"];
    let want_results = want["results"].as_array().unwrap();
    let got_ids: Vec<i64> = got.results.iter().map(|r| r.id).collect();
    let want_ids: Vec<i64> = want_results
        .iter()
        .map(|r| r["id"].as_i64().unwrap())
        .collect();
    if got_ids != want_ids {
        return Err(format!("ids {got_ids:?}, want {want_ids:?}"));
    }
    for (g, w) in got.results.iter().zip(want_results) {
        let mut diff = g.rrf_score != f(&w["rrf_score"]) || g.confidence != f(&w["confidence"]);
        match (&g.graph, w.get("match_type")) {
            (None, None) => {}
            (Some(gf), Some(mt)) => {
                diff |= gf.match_type.as_str() != mt.as_str().unwrap()
                    || gf.base_rrf_score != f(&w["base_rrf_score"])
                    || gf.graph_support != f(&w["graph_support"])
                    || gf.graph_via != ids(&w["graph_via"]);
            }
            _ => diff = true,
        }
        if diff {
            return Err(format!("result {} = {g:?}, want {w}", g.id));
        }
    }
    match (&got.graph, want.get("graph_info")) {
        (None, None) => Ok(()),
        (Some(gi), Some(wi)) => {
            let seeds: Vec<(i64, f64)> = wi["seeds"]
                .as_array()
                .unwrap()
                .iter()
                .map(|x| (x["id"].as_i64().unwrap(), f(&x["rrf_score"])))
                .collect();
            let same = gi.seeds == seeds
                && Some(gi.neighbors_found as u64) == wi["neighbors_found"].as_u64()
                && Some(gi.neighbors_filtered as u64) == wi["neighbors_filtered"].as_u64()
                && Some(gi.neighbors_added as u64) == wi["neighbors_added"].as_u64()
                && Some(gi.ppr_iterations as u64) == wi["ppr_iterations"].as_u64()
                && gi.ppr_alpha == f(&wi["ppr_alpha"]);
            if same {
                Ok(())
            } else {
                Err(format!("graph info {gi:?}, want {wi}"))
            }
        }
        _ => Err("graph info present on one side only".into()),
    }
}

#[test]
fn hybrid_search() {
    let cases = cases("hybrid");
    let bad = cases
        .iter()
        .enumerate()
        .filter_map(|(i, c)| hybrid_case(c).err().map(|e| format!("case {i}: {e}")))
        .collect();
    report("hybrid", cases.len(), bad);
}
