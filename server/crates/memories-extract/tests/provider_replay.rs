//! Replay tests: the Rust provider must send the same request as the Python
//! provider (v5.19.0) for the same input. `record_python.py` wrote the files.

use std::collections::HashMap;
use std::path::PathBuf;

use memories_extract::provider::Provider;
use memories_extract::replay::ReplayServer;
use serde_json::{Value, json};

// The same fake secrets as record_python.py.
const SECRETS: [(&str, &str); 4] = [
    ("ANTHROPIC_API_KEY", "sk-ant-api03-test-secret"),
    ("ANTHROPIC_OAUTH_TOKEN", "sk-ant-oat01-test-secret"),
    ("OPENAI_API_KEY", "sk-proj-test-secret"),
    ("OMLX_API_KEY", "omlx-test-secret"),
];

fn replay_files() -> Vec<(String, Value)> {
    let dir = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("tests/replay");
    let mut out: Vec<_> = std::fs::read_dir(&dir)
        .unwrap()
        .map(|e| e.unwrap().path())
        .filter(|p| p.extension().is_some_and(|x| x == "json"))
        .map(|p| {
            let text = std::fs::read_to_string(&p).unwrap();
            (p.file_name().unwrap().to_string_lossy().into_owned(), text)
        })
        .map(|(name, text)| {
            for (_, secret) in SECRETS {
                assert!(!text.contains(secret), "{name} holds a secret");
            }
            (name, serde_json::from_str(&text).unwrap())
        })
        .collect();
    out.sort_by(|a, b| a.0.cmp(&b.0));
    assert!(out.len() >= 10, "replay files are missing");
    out
}

async fn run_case(case: &Value) {
    let name = case["case"].as_str().unwrap();
    let reply = &case["response"];
    let server = ReplayServer::start(
        vec![(
            reply["status"].as_u64().unwrap() as u16,
            reply["body"].clone(),
        )],
        SECRETS.iter().map(|(_, s)| s.to_string()).collect(),
    )
    .await;

    let mut env: HashMap<String, String> = HashMap::new();
    for (var, value) in case["env"].as_object().unwrap() {
        let value = match value.as_str() {
            Some(v) => v.to_string(),
            None => SECRETS
                .iter()
                .find(|(k, _)| k == var)
                .unwrap()
                .1
                .to_string(),
        };
        env.insert(var.clone(), value);
    }
    env.insert(
        case["base_url_env"].as_str().unwrap().to_string(),
        format!(
            "{}{}",
            server.base_url(),
            case["base_url_suffix"].as_str().unwrap()
        ),
    );

    let provider = Provider::from_env(
        case["provider"].as_str(),
        case["model"].as_str(),
        |k: &str| env.get(k).cloned(),
    )
    .unwrap()
    .unwrap();

    let call = &case["call"];
    let mut result = if call["kind"] == "health" {
        json!({"healthy": provider.health_check().await})
    } else {
        let c = provider
            .complete(
                call["system"].as_str().unwrap(),
                call["user"].as_str().unwrap(),
            )
            .await
            .unwrap_or_else(|e| panic!("{name}: {e}"));
        json!({"text": c.text, "input_tokens": c.input_tokens, "output_tokens": c.output_tokens})
    };
    result["model"] = json!(provider.model());

    let requests = server.requests();
    assert_eq!(requests.len(), 1, "{name}: request count");
    assert_eq!(
        requests[0], case["request"],
        "{name}: request differs from Python"
    );
    assert_eq!(result, case["result"], "{name}: result differs from Python");
}

#[tokio::test]
async fn each_provider_sends_the_python_request() {
    let cases = replay_files();
    for provider in ["anthropic", "openai", "omlx", "ollama"] {
        assert!(
            cases.iter().any(|(_, c)| c["provider"] == provider),
            "no replay file for {provider}"
        );
    }
    for (_, case) in &cases {
        run_case(case).await;
    }
}
