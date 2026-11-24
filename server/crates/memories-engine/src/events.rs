//! The event bus of `event_bus.py`: the five event types and a history of the
//! last 100 events. The server gives them to the stream route and the webhooks.

use std::collections::VecDeque;
use std::sync::Mutex;
use std::time::Instant;

use serde_json::{Value, json};
use tokio::sync::broadcast;

use crate::rank::PyDateTime;

pub const EVENT_TYPES: [&str; 5] = [
    "memory.added",
    "memory.updated",
    "memory.deleted",
    "memory.linked",
    "extraction.completed",
];
const HISTORY: usize = 100;

#[derive(Clone, Debug, PartialEq)]
pub struct Event {
    pub event_type: &'static str,
    pub data: Value,
    pub timestamp: String,
    /// Python uses `time.monotonic_ns()`.
    pub id: String,
}

impl Event {
    /// `Event.to_dict`.
    pub fn to_json(&self) -> Value {
        json!({"type": self.event_type, "data": self.data,
               "timestamp": self.timestamp, "id": self.id})
    }

    /// `Event.to_sse`: one server-sent event, with the data as `json.dumps`.
    pub fn to_sse(&self) -> String {
        format!(
            "id: {}\nevent: {}\ndata: {}\n\n",
            self.id,
            self.event_type,
            py_dumps(&self.data)
        )
    }
}

/// Python `json.dumps(v)`: `", "` and `": "` between items, and only ASCII
/// (`\uXXXX` for each other character). Keys come in the `serde_json` order.
/// shortcut: a float uses the `serde_json` text, not the Python `repr`; the
/// event data holds no float now. Upgrade if an event gets a float field.
pub fn py_dumps(v: &Value) -> String {
    let mut out = String::new();
    write_py(v, &mut out);
    out
}

fn write_py(v: &Value, out: &mut String) {
    match v {
        Value::String(s) => {
            for c in serde_json::to_string(s).expect("a string").chars() {
                if c.is_ascii() && c != '\u{7f}' {
                    out.push(c);
                } else {
                    let mut buf = [0u16; 2];
                    for u in c.encode_utf16(&mut buf) {
                        out.push_str(&format!("\\u{u:04x}"));
                    }
                }
            }
        }
        Value::Array(a) => {
            out.push('[');
            for (i, x) in a.iter().enumerate() {
                if i > 0 {
                    out.push_str(", ");
                }
                write_py(x, out);
            }
            out.push(']');
        }
        Value::Object(m) => {
            out.push('{');
            for (i, (k, x)) in m.iter().enumerate() {
                if i > 0 {
                    out.push_str(", ");
                }
                write_py(&Value::String(k.clone()), out);
                out.push_str(": ");
                write_py(x, out);
            }
            out.push('}');
        }
        other => out.push_str(&other.to_string()),
    }
}

pub struct EventBus {
    history: Mutex<VecDeque<Event>>,
    tx: broadcast::Sender<Event>,
    start: Instant,
}

impl Default for EventBus {
    fn default() -> Self {
        Self {
            history: Mutex::new(VecDeque::with_capacity(HISTORY)),
            tx: broadcast::channel(256).0,
            start: Instant::now(),
        }
    }
}

impl EventBus {
    /// Record and send an event. An unknown type is dropped, as in Python.
    pub fn emit(&self, event_type: &str, data: Value) {
        let Some(&event_type) = EVENT_TYPES.iter().find(|t| **t == event_type) else {
            tracing::warn!("Unknown event type: {event_type}");
            return;
        };
        let event = Event {
            event_type,
            data,
            timestamp: PyDateTime::now_utc().isoformat_utc(),
            id: self.start.elapsed().as_nanos().to_string(),
        };
        let mut h = self.history.lock().unwrap_or_else(|e| e.into_inner());
        if h.len() == HISTORY {
            h.pop_front();
        }
        h.push_back(event.clone());
        // No subscriber is not an error.
        let _ = self.tx.send(event);
    }

    /// `recent_events(limit)`: the last `limit` events, oldest first.
    pub fn recent(&self, limit: usize) -> Vec<Event> {
        let h = self.history.lock().unwrap_or_else(|e| e.into_inner());
        h.iter()
            .skip(h.len().saturating_sub(limit))
            .cloned()
            .collect()
    }

    pub fn subscribe(&self) -> broadcast::Receiver<Event> {
        self.tx.subscribe()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn keeps_the_last_100_known_events() {
        let bus = EventBus::default();
        let mut rx = bus.subscribe();
        bus.emit("memory.unknown", json!({}));
        for i in 0..105 {
            bus.emit("memory.added", json!({"id": i}));
        }
        let all = bus.recent(1000);
        assert_eq!(all.len(), 100);
        assert_eq!(all[0].data, json!({"id": 5}));
        assert_eq!(bus.recent(2)[1].data, json!({"id": 104}));
        let first = rx.try_recv().unwrap();
        assert_eq!(first.event_type, "memory.added");
        assert!(first.timestamp.ends_with("+00:00"));
        assert!(first.timestamp.as_str() > "2026-11-16T00");
        assert_eq!(first.to_json()["data"], json!({"id": 0}));
    }

    #[test]
    fn sse_text_has_the_python_json_dumps_data() {
        let e = Event {
            event_type: "memory.updated",
            data: json!({"id": 7, "source": "é/😀", "updated_fields": ["a", "b"], "x": null, "ok": true}),
            timestamp: "t".into(),
            id: "42".into(),
        };
        // Python: json.dumps({...}) with the same key order.
        assert_eq!(
            e.to_sse(),
            "id: 42\nevent: memory.updated\ndata: {\"id\": 7, \"ok\": true, \"source\": \"\\u00e9/\\ud83d\\ude00\", \"updated_fields\": [\"a\", \"b\"], \"x\": null}\n\n"
        );
        assert_eq!(
            py_dumps(&json!("a\"\\\n\u{1f}\u{7f}")),
            r#""a\"\\\n\u001f\u007f""#
        );
    }
}
