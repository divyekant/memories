//! The reinforcement buffer. A search sets `last_reinforced_at` of its hits
//! (`MemoryEngine.reinforce`). Python changes the record in memory and saves
//! it later. The Rust server keeps the new values here and writes them to
//! Qdrant in the background. A read must call [`Reinforcements::overlay`],
//! so that it shows a value that is not written yet.

use std::collections::{BTreeMap, HashMap};
use std::sync::Mutex;

use memories_store::{QdrantStore, StoreError};
use memories_types::Record;
use serde_json::{Map, Value};

#[derive(Default)]
pub struct Reinforcements {
    pending: Mutex<HashMap<u64, String>>,
}

impl Reinforcements {
    fn lock(&self) -> std::sync::MutexGuard<'_, HashMap<u64, String>> {
        self.pending.lock().unwrap_or_else(|e| e.into_inner())
    }

    pub fn add(&self, ids: impl IntoIterator<Item = u64>, at: &str) {
        let mut p = self.lock();
        for id in ids {
            p.insert(id, at.to_owned());
        }
    }

    pub fn get(&self, id: u64) -> Option<String> {
        self.lock().get(&id).cloned()
    }

    /// Sets the pending `last_reinforced_at` of `record`, if there is one.
    pub fn overlay(&self, record: &mut Record) {
        if let Some(at) = self.get(record.id) {
            record.last_reinforced_at = Some(at);
            record.extra.remove("last_reinforced_at");
        }
    }

    /// Drops the pending value of a deleted record.
    pub fn forget(&self, id: u64) {
        self.lock().remove(&id);
    }

    pub fn len(&self) -> usize {
        self.lock().len()
    }

    pub fn is_empty(&self) -> bool {
        self.len() == 0
    }

    /// Writes the pending values, one Qdrant call for each time value. On an
    /// error, the values not written go back, unless a newer value came in.
    pub async fn flush(&self, store: &QdrantStore) -> Result<usize, StoreError> {
        let taken: HashMap<u64, String> = std::mem::take(&mut *self.lock());
        let mut by_time: BTreeMap<String, Vec<u64>> = BTreeMap::new();
        for (id, at) in taken {
            by_time.entry(at).or_default().push(id);
        }
        let mut written = 0;
        let mut groups = by_time.into_iter();
        while let Some((at, mut ids)) = groups.next() {
            ids.sort_unstable();
            let mut fields = Map::new();
            fields.insert("last_reinforced_at".into(), Value::String(at.clone()));
            if let Err(e) = store.set_payload_existing(&ids, fields).await {
                let mut p = self.lock();
                for (at, ids) in std::iter::once((at, ids)).chain(groups) {
                    for id in ids {
                        p.entry(id).or_insert_with(|| at.clone());
                    }
                }
                return Err(e);
            }
            written += ids.len();
        }
        Ok(written)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn overlay_sets_the_pending_value() {
        let r = Reinforcements::default();
        r.add([1, 2], "2026-11-16T00:00:00+00:00");
        r.add([2], "2026-11-16T00:00:01+00:00");
        let mut rec = Record::from_json(serde_json::json!({"id": 2, "text": "t"})).unwrap();
        r.overlay(&mut rec);
        assert_eq!(
            rec.last_reinforced_at.as_deref(),
            Some("2026-11-16T00:00:01+00:00")
        );
        r.forget(2);
        assert_eq!(r.len(), 1);
    }
}
