//! The in-memory catalog of the engine: the source map (each distinct source
//! and its record count) and the link map (the source, archive state and
//! links of each record). Python reads these from `self.metadata`; the Rust
//! server keeps them here, so that a search does not scroll the store.
//!
//! Each write must call [`Catalog::put`] or [`Catalog::remove`].

use std::collections::BTreeMap;

use memories_types::Record;

use crate::rank::graph::{Adjacency, build_adjacency};
use crate::records::truthy;

#[derive(Clone, Debug, Default, PartialEq)]
pub struct Entry {
    /// `str(meta.get("source", ""))`.
    pub source: String,
    /// `meta.get("archived")` is truthy.
    pub archived: bool,
    /// `(to_id, type)` of each link, in stored order.
    pub links: Vec<(i64, String)>,
}

impl Entry {
    pub fn of(record: &Record) -> Self {
        let source = match &record.source {
            Some(s) => s.clone(),
            None => match record.extra.get("source") {
                None => String::new(),
                Some(serde_json::Value::String(s)) => s.clone(),
                Some(v) => crate::records::py_str(v),
            },
        };
        let archived = match record.archived {
            Some(b) => b,
            None => record.extra.get("archived").is_some_and(truthy),
        };
        let links = record
            .links
            .iter()
            .flatten()
            .map(|l| (l.to_id, l.link_type.clone()))
            .collect();
        Self {
            source,
            archived,
            links,
        }
    }
}

/// Records in ID order. The Python order of `self.metadata` is the add order,
/// which is the ID order after a load.
#[derive(Debug, Default)]
pub struct Catalog {
    entries: BTreeMap<u64, Entry>,
    sources: BTreeMap<String, u64>,
}

impl Catalog {
    pub fn put(&mut self, record: &Record) {
        self.remove(record.id);
        let entry = Entry::of(record);
        *self.sources.entry(entry.source.clone()).or_insert(0) += 1;
        self.entries.insert(record.id, entry);
    }

    pub fn remove(&mut self, id: u64) -> Option<Entry> {
        let old = self.entries.remove(&id)?;
        if let Some(n) = self.sources.get_mut(&old.source) {
            *n -= 1;
            if *n == 0 {
                self.sources.remove(&old.source);
            }
        }
        Some(old)
    }

    pub fn len(&self) -> usize {
        self.entries.len()
    }

    pub fn is_empty(&self) -> bool {
        self.entries.is_empty()
    }

    pub fn contains(&self, id: i64) -> bool {
        u64::try_from(id).is_ok_and(|id| self.entries.contains_key(&id))
    }

    pub fn get(&self, id: i64) -> Option<&Entry> {
        self.entries.get(&u64::try_from(id).ok()?)
    }

    /// Each record ID and entry, in ID order.
    pub fn iter(&self) -> impl Iterator<Item = (u64, &Entry)> {
        self.entries.iter().map(|(&id, e)| (id, e))
    }

    /// The records with a link to `id`, in ID order.
    pub fn ids_linking_to(&self, id: i64) -> Vec<u64> {
        self.entries
            .iter()
            .filter(|(_, e)| e.links.iter().any(|(to, _)| *to == id))
            .map(|(&from, _)| from)
            .collect()
    }

    /// The distinct sources, sorted, with their record counts.
    pub fn sources(&self) -> &BTreeMap<String, u64> {
        &self.sources
    }

    /// The `related_to` graph of `_build_adjacency`, from the links in the
    /// order of the stored records.
    pub fn adjacency(&self) -> Adjacency {
        // shortcut: builds the graph for each search (Python caches it until a
        // write); cache it with a write counter if the link count grows large.
        let links = self.entries.iter().flat_map(|(&from, e)| {
            e.links
                .iter()
                .map(move |(to, kind)| (from as i64, *to, kind.as_str()))
        });
        build_adjacency(links, |id| self.contains(id))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn record(v: serde_json::Value) -> Record {
        Record::from_json(v).unwrap()
    }

    #[test]
    fn source_map_counts_follow_writes() {
        let mut c = Catalog::default();
        c.put(&record(json!({"id": 1, "text": "a", "source": "x/a"})));
        c.put(&record(json!({"id": 2, "text": "b", "source": "x/a"})));
        c.put(&record(json!({"id": 3, "text": "c"})));
        assert_eq!(c.sources().get("x/a"), Some(&2));
        assert_eq!(c.sources().get(""), Some(&1));
        c.put(&record(json!({"id": 2, "text": "b", "source": "y"})));
        c.remove(1);
        assert_eq!(c.sources().get("x/a"), None);
        assert_eq!(c.sources().get("y"), Some(&1));
        assert_eq!(c.len(), 2);
        assert!(c.remove(9).is_none());
    }

    #[test]
    fn link_map_builds_the_related_graph() {
        let mut c = Catalog::default();
        c.put(&record(json!({"id": 1, "text": "a", "archived": "yes",
            "links": [{"to_id": 2, "type": "related_to"}, {"to_id": 3, "type": "supersedes"}]})));
        c.put(&record(
            json!({"id": 2, "text": "b", "links": [{"to_id": 9, "type": "related_to"}]}),
        ));
        c.put(&record(json!({"id": 3, "text": "c"})));
        assert!(c.get(1).unwrap().archived);
        let adj = c.adjacency();
        assert_eq!(adj.keys().collect::<Vec<_>>(), vec![1, 2]);
        assert!(adj.get(1).unwrap().contains(2));
    }
}
