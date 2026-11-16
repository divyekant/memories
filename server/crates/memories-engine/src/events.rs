//! The event bus of `event_bus.py`: the five event types and a history of the
//! last 100 events. Task P5-5 adds the stream route and the webhooks.

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
}
