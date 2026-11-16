//! The golden cases of `parity/extract_golden.py`: the text rules of the
//! extraction pipeline against the Python code of v5.19.0.

use memories_engine::policy::redact_secrets;
use memories_extract::text::{
    self, Fact, FallbackConfig, audn_prompt, clean_transcript, clip_text, debug_similar,
    extraction_system_prompt, fallback_facts, parse_decisions, parse_facts, parse_json_array,
    parse_single_call, single_call_prompts,
};
use serde_json::{Value, json};

fn cases(f: &str) -> Vec<Value> {
    let path = concat!(
        env!("CARGO_MANIFEST_DIR"),
        "/../../../parity/golden/extract/text.ndjson"
    );
    let all: Vec<Value> = std::fs::read_to_string(path)
        .expect("golden file")
        .lines()
        .map(|l| serde_json::from_str(l).unwrap())
        .collect();
    let out: Vec<Value> = all.into_iter().filter(|c| c["fn"] == f).collect();
    assert!(!out.is_empty(), "no case for {f}");
    out
}

fn s(v: &Value) -> &str {
    v.as_str().unwrap()
}

fn rules(c: &Value) -> Option<&Value> {
    Some(&c["rules"]).filter(|r| !r.is_null())
}

#[test]
fn clean_transcript_matches_python() {
    let mut bad = 0;
    for c in cases("clean_transcript") {
        let got = clean_transcript(s(&c["input"]));
        if got != s(&c["output"]) {
            bad += 1;
            eprintln!(
                "input {:?}\n  want {:?}\n  got  {got:?}",
                c["input"], c["output"]
            );
        }
    }
    assert_eq!(bad, 0);
}

#[test]
fn redact_secrets_matches_python() {
    let mut bad = 0;
    for c in cases("redact_secrets") {
        let (t, types) = redact_secrets(s(&c["input"]));
        if json!([t, types]) != c["output"] {
            bad += 1;
            eprintln!(
                "input {:?}\n  want {}\n  got  {t:?} {types:?}",
                c["input"], c["output"]
            );
        }
    }
    assert_eq!(bad, 0);
}

#[test]
fn fallback_facts_match_python() {
    let cfg = FallbackConfig::default();
    for c in cases("fallback_facts") {
        assert_eq!(
            json!(fallback_facts(s(&c["input"]), &cfg)),
            c["output"],
            "{:?}",
            c["input"]
        );
    }
}

#[test]
fn json_parse_rules_match_python() {
    for c in cases("parse_json_array") {
        assert_eq!(
            json!(parse_json_array(s(&c["input"]))),
            c["output"],
            "{:?}",
            c["input"]
        );
    }
    for c in cases("clip_text") {
        let n = c["max"].as_u64().unwrap() as usize;
        assert_eq!(clip_text(s(&c["input"]), n), s(&c["output"]));
    }
}

#[test]
fn extraction_prompts_match_python() {
    for c in cases("extraction_system_prompt") {
        let got = extraction_system_prompt(s(&c["source"]), s(&c["context"]), rules(&c));
        assert_eq!(
            got,
            s(&c["output"]),
            "{} {} {}",
            c["source"],
            c["context"],
            c["rules"]
        );
    }
}

#[test]
fn fact_replies_match_python() {
    for c in cases("extract_facts") {
        let (n, chars) = (
            c["max_facts"].as_u64().unwrap(),
            c["max_chars"].as_u64().unwrap(),
        );
        let got = parse_facts(s(&c["reply"]), n as usize, chars as usize);
        let want_err = c["error"].as_bool().unwrap();
        assert_eq!(got.is_err(), want_err, "{}", c["reply"]);
        let facts: Vec<Value> = got.unwrap_or_default().iter().map(Fact::to_json).collect();
        assert_eq!(json!(facts), c["output"], "{}", c["reply"]);
    }
}

#[test]
fn audn_prompts_and_decisions_match_python() {
    for c in cases("run_audn") {
        let facts: Vec<Fact> = c["facts"]
            .as_array()
            .unwrap()
            .iter()
            .map(|f| Fact {
                category: s(&f["category"]).into(),
                text: s(&f["text"]).into(),
            })
            .collect();
        let similar: Vec<Vec<Value>> = facts
            .iter()
            .map(|f| {
                c["similar"][&f.text]
                    .as_array()
                    .cloned()
                    .unwrap_or_default()
            })
            .collect();
        let prompt = audn_prompt(&facts, &similar, rules(&c), 500, 280, 5);
        assert_eq!(prompt, s(&c["prompt"]["user"]));
        assert_eq!(s(&c["prompt"]["system"]), text::AUDN_SYSTEM);
        for (i, m) in similar.iter().enumerate() {
            assert_eq!(
                json!(debug_similar(m, 280, 5)),
                c["debug_similar"][i.to_string()]
            );
        }
        let want = &c["output"];
        match c["reply"].as_str().map(parse_decisions) {
            Some(Ok(d)) => assert_eq!(&json!(d), want),
            _ => {
                let fb: Vec<Value> = (0..facts.len())
                    .map(|i| json!({"action": "FALLBACK_ADD", "fact_index": i}))
                    .collect();
                assert_eq!(&json!(fb), want);
                assert_eq!(c["tokens"], json!({"input": 0, "output": 0}));
            }
        }
    }
}

#[test]
fn single_call_matches_python() {
    for c in cases("single_call") {
        let n = c["max_facts"].as_u64().unwrap() as usize;
        let (system, user) = single_call_prompts(s(&c["messages"]), rules(&c), n);
        assert_eq!(system, s(&c["prompt"]["system"]));
        assert_eq!(user, s(&c["prompt"]["user"]));
        assert_eq!(
            json!(parse_single_call(s(&c["reply"]), n).unwrap()),
            c["output"]
        );
    }
}

#[test]
fn profiles_match_python() {
    use memories_extract::profiles::ExtractionProfiles;
    let dir = std::env::temp_dir().join(format!("p43_profiles_{}", std::process::id()));
    let _ = std::fs::remove_dir_all(&dir);
    std::fs::create_dir_all(&dir).unwrap();
    let path = dir.join("extraction_profiles.json");
    let mut p = ExtractionProfiles::open(&path).unwrap();
    for c in cases("profiles") {
        let op = c["op"].as_array().unwrap();
        let arg = || s(&op[1]);
        let got = match s(&op[0]) {
            "put" => json!(p.put(arg(), op[2].as_object().unwrap()).unwrap()),
            "get" => json!(p.get(arg())),
            "delete" => json!(p.delete(arg()).unwrap()),
            "list" => json!(p.list_all()),
            "resolve" => json!(p.resolve(arg())),
            _ => {
                // The Python server must read the file: same content after a parse.
                let text = std::fs::read_to_string(&path).unwrap();
                assert_eq!(serde_json::from_str::<Value>(&text).unwrap(), c["output"]);
                let order: Vec<String> = ExtractionProfiles::open(&path)
                    .unwrap()
                    .list_all()
                    .iter()
                    .map(|m| m["source_prefix"].as_str().unwrap().to_owned())
                    .collect();
                assert_eq!(order, ["claude-code/app/", "codex/", "z/"]);
                continue;
            }
        };
        assert_eq!(got, c["output"], "{op:?}");
    }
    let _ = std::fs::remove_dir_all(&dir);
}
