//! Golden cases of `parity/search_golden.py` that need no server.

use memories_engine::evidence::build_evidence_packet;
use serde_json::Value;

fn cases(name: &str) -> Vec<Value> {
    let path = format!(
        "{}/../../../parity/golden/search/{name}",
        env!("CARGO_MANIFEST_DIR")
    );
    std::fs::read_to_string(&path)
        .unwrap_or_else(|e| panic!("{path}: {e}"))
        .lines()
        .map(|l| serde_json::from_str(l).expect("json"))
        .collect()
}

#[test]
fn evidence_packets_match_python() {
    let all = cases("evidence.ndjson");
    assert!(all.len() > 50);
    for (i, c) in all.iter().enumerate() {
        let got = build_evidence_packet(
            c["query"].as_str().unwrap(),
            c["results"].as_array().unwrap(),
        );
        assert_eq!(got, c["packet"], "case {i}: {}", c["query"]);
    }
}
