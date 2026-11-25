//! Exit check of task P3-3: 100 forced stops (SIGKILL) during a constant load
//! of add, batch add, supersede and merge leave no record in a state between
//! two steps. The test runs the real binary on one `DATA_DIR` and one Qdrant
//! collection, and checks the store after each start.
//!
//! Run: QDRANT_TEST_URL=http://127.0.0.1:6333 \
//!   cargo test -p memories-server --test write_crash -- --ignored --nocapture

use std::collections::{BTreeMap, HashMap};
use std::net::TcpListener;
use std::path::Path;
use std::process::{Child, Command};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant, SystemTime};

use serde_json::{Value, json};

const KEY: &str = "write-crash-key";
const STOPS: usize = 100;
const SOURCE: &str = "test/crash";

fn free_port() -> u16 {
    TcpListener::bind("127.0.0.1:0")
        .unwrap()
        .local_addr()
        .unwrap()
        .port()
}

/// A number from the clock: enough to spread the stops over the steps.
fn jitter(max: u64) -> u64 {
    let n = SystemTime::now()
        .duration_since(SystemTime::UNIX_EPOCH)
        .unwrap()
        .subsec_nanos() as u64;
    n.wrapping_mul(6364136223846793005) % max
}

fn start(data: &Path, port: u16, qdrant: &str, collection: &str, log: &Path) -> Child {
    let log = std::fs::OpenOptions::new()
        .create(true)
        .append(true)
        .open(log)
        .unwrap();
    Command::new(env!("CARGO_BIN_EXE_memories-server"))
        .args(["serve", "--port", &port.to_string()])
        .env("API_KEY", KEY)
        .env("DATA_DIR", data)
        .env("QDRANT_URL", qdrant)
        .env("QDRANT_COLLECTION", collection)
        .stdout(log.try_clone().unwrap())
        .stderr(log)
        .spawn()
        .unwrap()
}

async fn wait_up(http: &reqwest::Client, base: &str, child: &mut Child) {
    let deadline = Instant::now() + Duration::from_secs(120);
    while http.get(format!("{base}/health")).send().await.is_err() {
        assert!(
            child.try_wait().unwrap().is_none(),
            "the server stopped at start"
        );
        assert!(Instant::now() < deadline, "the server did not start");
        tokio::time::sleep(Duration::from_millis(50)).await;
    }
}

async fn post(http: &reqwest::Client, url: String, body: Value) -> Option<Value> {
    let r = http
        .post(url)
        .header("X-API-Key", KEY)
        .json(&body)
        .send()
        .await
        .ok()?;
    if !r.status().is_success() {
        return None;
    }
    r.json().await.ok()
}

/// A client with no record to use waits for the add clients.
async fn idle() -> Option<()> {
    tokio::time::sleep(Duration::from_millis(20)).await;
    None
}

/// One client: the same write again and again until the server stops.
async fn load(http: reqwest::Client, base: String, kind: usize, ids: Arc<Mutex<Vec<u64>>>) -> u64 {
    let mut done = 0;
    for n in 0u64.. {
        let pick = |k: usize| {
            let ids = ids.lock().unwrap();
            (ids.len() > k).then(|| {
                let at = jitter(ids.len() as u64 - k as u64) as usize;
                ids[at..at + k].to_vec()
            })
        };
        let text = format!(
            "note {kind}-{n}-{} about the deploy plan",
            jitter(1_000_000)
        );
        let ok = match kind {
            0 => {
                let r = post(
                    &http,
                    format!("{base}/memory/add"),
                    json!({"text": text, "source": SOURCE}),
                )
                .await;
                r.map(|r| ids.lock().unwrap().push(r["id"].as_u64().unwrap()))
            }
            1 => {
                // 150 items: two chunks of points.
                let items: Vec<Value> = (0..150)
                    .map(|i| json!({"text": format!("{text} item {i}"), "source": SOURCE}))
                    .collect();
                post(
                    &http,
                    format!("{base}/memory/add-batch"),
                    json!({"memories": items}),
                )
                .await
                .map(|_| ())
            }
            2 => match pick(1) {
                Some(old) => {
                    let body = json!({"old_id": old[0], "new_text": text, "source": SOURCE});
                    let r = post(&http, format!("{base}/memory/supersede"), body).await;
                    r.map(|r| ids.lock().unwrap().push(r["new_id"].as_u64().unwrap()))
                }
                None => idle().await,
            },
            _ => match pick(2) {
                Some(two) => {
                    let body = json!({"ids": two, "merged_text": text, "source": SOURCE});
                    let r = post(&http, format!("{base}/memory/merge"), body).await;
                    r.map(|r| ids.lock().unwrap().push(r["id"].as_u64().unwrap()))
                }
                None => idle().await,
            },
        };
        if ok.is_some() {
            done += 1;
        } else if http.get(format!("{base}/health")).send().await.is_err() {
            return done;
        }
    }
    done
}

/// Every point of the collection: ID to payload.
async fn points(http: &reqwest::Client, qdrant: &str, collection: &str) -> BTreeMap<u64, Value> {
    let mut out = BTreeMap::new();
    let mut offset = Value::Null;
    loop {
        let body =
            json!({"limit": 1000, "with_payload": true, "with_vector": false, "offset": offset});
        let r: Value = http
            .post(format!("{qdrant}/collections/{collection}/points/scroll"))
            .json(&body)
            .send()
            .await
            .unwrap()
            .json()
            .await
            .unwrap();
        for p in r["result"]["points"].as_array().unwrap() {
            out.insert(p["id"].as_u64().unwrap(), p["payload"].clone());
        }
        offset = r["result"]["next_page_offset"].clone();
        if offset.is_null() {
            return out;
        }
    }
}

async fn count_without(
    http: &reqwest::Client,
    qdrant: &str,
    collection: &str,
    vector: &str,
) -> u64 {
    let body = json!({"exact": true, "filter": {"must_not": [{"has_vector": vector}]}});
    let r: Value = http
        .post(format!("{qdrant}/collections/{collection}/points/count"))
        .json(&body)
        .send()
        .await
        .unwrap()
        .json()
        .await
        .unwrap();
    r["result"]["count"]
        .as_u64()
        .unwrap_or_else(|| panic!("{r}"))
}

/// The rules of a store with no write between two steps.
async fn check(http: &reqwest::Client, qdrant: &str, collection: &str, data: &Path, stop: usize) {
    let state = memories_store::StateDb::open(&data.join("state.db")).unwrap();
    assert!(
        state.pending_ops().unwrap().is_empty(),
        "stop {stop}: a journal entry stays"
    );
    for v in ["dense", "keyword"] {
        assert_eq!(
            count_without(http, qdrant, collection, v).await,
            0,
            "stop {stop}: a point has no {v} vector"
        );
    }
    let all = points(http, qdrant, collection).await;
    let mut newest: HashMap<u64, u64> = HashMap::new();
    for (&id, p) in &all {
        for f in ["text", "source", "created_at", "updated_at"] {
            assert!(p[f].is_string(), "stop {stop}: record {id} has no {f}: {p}");
        }
        if let Some(old) = p["supersedes"].as_u64() {
            let n = newest.entry(old).or_insert(id);
            *n = (*n).max(id);
        }
        for l in p["links"].as_array().into_iter().flatten() {
            if l["type"] == "supersedes" {
                let to = l["to_id"].as_u64().unwrap();
                if let Some(target) = all.get(&to) {
                    assert_eq!(
                        target["archived"],
                        json!(true),
                        "stop {stop}: {id} supersedes {to}, which is not archived"
                    );
                }
            }
        }
    }
    for (old, new) in newest {
        if let Some(p) = all.get(&old) {
            assert_eq!(
                p["superseded_by"],
                json!(new),
                "stop {stop}: {old} is not superseded by {new}"
            );
        }
    }
}

#[tokio::test(flavor = "multi_thread")]
#[ignore = "needs QDRANT_TEST_URL and the embedding model"]
async fn forced_stops_leave_no_record_between_two_steps() {
    let qdrant = std::env::var("QDRANT_TEST_URL").expect("set QDRANT_TEST_URL");
    let name = format!("p33_crash_{}", std::process::id());
    let collection = format!("{name}_v2");
    let data = std::env::temp_dir().join(&name);
    let _ = std::fs::remove_dir_all(&data);
    let http = reqwest::Client::builder().no_proxy().build().unwrap();
    let ids = Arc::new(Mutex::new(Vec::new()));
    let mut writes = [0u64; 4];
    let started = Instant::now();
    let log = std::env::temp_dir().join(format!("{name}.log"));
    let _ = std::fs::remove_file(&log);

    for stop in 0..=STOPS {
        let port = free_port();
        let base = format!("http://127.0.0.1:{port}");
        let mut child = start(&data, port, &qdrant, &name, &log);
        wait_up(&http, &base, &mut child).await;
        if stop > 0 {
            check(&http, &qdrant, &collection, &data, stop).await;
        }
        if stop == STOPS {
            child.kill().unwrap();
            child.wait().unwrap();
            break;
        }
        // The IDs that a client can supersede or merge: the records that are
        // not archived.
        let live: Vec<u64> = points(&http, &qdrant, &collection)
            .await
            .into_iter()
            .filter(|(_, p)| p["archived"] != json!(true))
            .map(|(id, _)| id)
            .collect();
        *ids.lock().unwrap() = live;
        let clients: Vec<_> = (0..4)
            .map(|k| tokio::spawn(load(http.clone(), base.clone(), k, ids.clone())))
            .collect();
        tokio::time::sleep(Duration::from_millis(200 + jitter(1_300))).await;
        child.kill().unwrap(); // SIGKILL: no shutdown code runs.
        child.wait().unwrap();
        for (k, c) in clients.into_iter().enumerate() {
            writes[k] += c.await.unwrap();
        }
    }
    let n = points(&http, &qdrant, &collection).await.len();
    let logs = std::fs::read_to_string(&log).unwrap_or_default();
    let mut repaired: BTreeMap<String, usize> = BTreeMap::new();
    for line in logs
        .lines()
        .filter(|l| l.contains("completed a write that a stop interrupted"))
    {
        for op in ["supersede", "merge", "delete"] {
            if line.contains(op) {
                *repaired.entry(op.into()).or_default() += 1;
            }
        }
    }
    println!(
        "{STOPS} forced stops, {n} records, {:.0} s, writes (add, batch, supersede, merge): {writes:?}, writes completed at start: {repaired:?}",
        started.elapsed().as_secs_f64()
    );
    assert!(
        writes.iter().all(|&w| w > 0),
        "a kind of write never succeeded: {writes:?}"
    );
    assert!(
        !repaired.is_empty(),
        "no stop fell between two steps of a write"
    );
    let _ = std::fs::remove_file(&log);
    let _ = http
        .delete(format!("{qdrant}/collections/{collection}"))
        .send()
        .await;
    let _ = std::fs::remove_dir_all(&data);
}
