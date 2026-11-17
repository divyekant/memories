//! The in-memory catalog of the engine: the source map (each distinct source
//! and its record count) and the link map (the source, archive state and
//! links of each record). Python reads these from `self.metadata`; the Rust
//! server keeps them here, so that a search does not scroll the store.
//!
//! Each write must call [`Catalog::put`] or [`Catalog::remove`].

use std::collections::{BTreeMap, HashMap};
use std::sync::{Arc, OnceLock};

use memories_types::Record;

use crate::rank::graph::{Adjacency, build_adjacency, filter_adjacency};
use crate::records::truthy;

#[derive(Clone, Debug, Default, PartialEq)]
pub struct Entry {
    /// `str(meta.get("source", ""))`.
    pub source: String,
    /// The stored source is a string or absent (Python string methods work on it).
    pub source_is_str: bool,
    /// `meta.get("archived")` is truthy.
    pub archived: bool,
    /// `(to_id, type)` of each link, in stored order.
    pub links: Vec<(i64, String)>,
    /// `source_memory_ids` is a list with more than one item (the exact reuse
    /// count of the `promotion` part of `GET /metrics`).
    pub multi_source: bool,
}

impl Entry {
    pub fn of(record: &Record) -> Self {
        let source_is_str = record.source.is_some()
            || matches!(
                record.extra.get("source"),
                None | Some(serde_json::Value::String(_))
            );
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
        let multi_source = record
            .extra
            .get("source_memory_ids")
            .and_then(serde_json::Value::as_array)
            .is_some_and(|ids| ids.len() > 1);
        Self {
            source,
            source_is_str,
            archived,
            links,
            multi_source,
        }
    }
}

/// Records in ID order. The Python order of `self.metadata` is the add order,
/// which is the ID order after a load.
#[derive(Debug, Default)]
pub struct Catalog {
    entries: BTreeMap<u64, Entry>,
    sources: BTreeMap<String, u64>,
    /// The `related_to` graph, built on the first search after a change to it.
    adjacency: OnceLock<Arc<Adjacency>>,
    /// The graph of the records that are not archived (the default scope).
    unarchived: OnceLock<Arc<Adjacency>>,
    /// How many `related_to` links point to each ID.
    targets: HashMap<i64, usize>,
}

fn related(e: &Entry) -> impl Iterator<Item = i64> + '_ {
    e.links
        .iter()
        .filter(|(_, kind)| kind == "related_to")
        .map(|(to, _)| *to)
}

impl Catalog {
    pub fn put(&mut self, record: &Record) {
        let old = self.take(record.id);
        let entry = Entry::of(record);
        for to in related(&entry) {
            *self.targets.entry(to).or_insert(0) += 1;
        }
        // A graph change: other links, or a new record that is a link target.
        let graph = match &old {
            Some(o) => o.links != entry.links,
            None => self.is_in_graph(record.id as i64, &entry),
        };
        let archive = old.as_ref().is_some_and(|o| o.archived != entry.archived)
            || (old.is_none() && entry.archived);
        self.forget_graphs(graph, archive);
        *self.sources.entry(entry.source.clone()).or_insert(0) += 1;
        self.entries.insert(record.id, entry);
    }

    pub fn remove(&mut self, id: u64) -> Option<Entry> {
        let old = self.take(id)?;
        let graph = self.is_in_graph(id as i64, &old);
        self.forget_graphs(graph, false);
        Some(old)
    }

    /// A record with `related_to` links, or one that such a link points to.
    fn is_in_graph(&self, id: i64, e: &Entry) -> bool {
        related(e).next().is_some() || self.targets.contains_key(&id)
    }

    fn forget_graphs(&mut self, graph: bool, archive: bool) {
        if graph {
            self.adjacency = OnceLock::new();
        }
        if graph || archive {
            self.unarchived = OnceLock::new();
        }
    }

    /// Removes the entry and its counts. Does not change the kept graphs.
    fn take(&mut self, id: u64) -> Option<Entry> {
        let old = self.entries.remove(&id)?;
        for to in related(&old) {
            if let Some(n) = self.targets.get_mut(&to) {
                *n -= 1;
                if *n == 0 {
                    self.targets.remove(&to);
                }
            }
        }
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

    /// The records in ID order.
    pub fn iter(&self) -> impl Iterator<Item = (u64, &Entry)> {
        self.entries.iter().map(|(id, e)| (*id, e))
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
    /// Built once and kept until the next write (as Python does). A build
    /// for each search cost most of the search CPU on production data.
    pub fn adjacency(&self) -> Arc<Adjacency> {
        self.adjacency
            .get_or_init(|| {
                let links = self.entries.iter().flat_map(|(&from, e)| {
                    e.links
                        .iter()
                        .map(move |(to, kind)| (from as i64, *to, kind.as_str()))
                });
                Arc::new(build_adjacency(links, |id| self.contains(id)))
            })
            .clone()
    }

    /// `filter_adjacency` of [`Self::adjacency`] for a scope that hides
    /// only archived records. Kept until a link or archive state changes.
    pub fn unarchived_adjacency(&self) -> Arc<Adjacency> {
        self.unarchived
            .get_or_init(|| {
                let full = self.adjacency();
                let visible = |id: i64| self.get(id).is_some_and(|e| !e.archived);
                Arc::new(filter_adjacency(&full, Some(&visible)).into_owned())
            })
            .clone()
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
        assert!(Arc::ptr_eq(&adj, &c.adjacency()), "kept until a write");
        // A write drops the kept graph: a new record can be a link target.
        c.put(&record(json!({"id": 9, "text": "i"})));
        let adj = c.adjacency();
        assert!(adj.get(2).unwrap().contains(9));
        c.remove(9);
        assert!(!c.adjacency().get(2).is_some_and(|n| n.contains(9)));
        // A write outside the graph keeps it.
        let adj = c.adjacency();
        c.put(&record(json!({"id": 10, "text": "j"})));
        c.put(&record(json!({"id": 3, "text": "c changed"})));
        c.remove(10);
        assert!(Arc::ptr_eq(&adj, &c.adjacency()));
    }

    fn snapshot(adj: &Adjacency) -> Vec<(i64, Vec<i64>)> {
        adj.iter().map(|(k, v)| (k, v.iter().collect())).collect()
    }

    /// The kept graphs equal a fresh build (content and order) after any
    /// sequence of puts and removes.
    #[test]
    fn kept_graphs_match_a_fresh_build() {
        let mut seed: u64 = 7;
        let mut next = |n: u64| {
            seed = seed.wrapping_mul(6364136223846793005).wrapping_add(1442695040888963407);
            (seed >> 33) % n
        };
        let mut c = Catalog::default();
        for step in 0..3000 {
            let id = 1 + next(40) as i64;
            if next(5) == 0 {
                c.remove(id as u64);
            } else {
                let links: Vec<_> = (0..next(3))
                    .map(|_| {
                        let kind = if next(4) == 0 { "supersedes" } else { "related_to" };
                        json!({"to_id": 1 + next(45), "type": kind})
                    })
                    .collect();
                c.put(&record(json!({"id": id, "text": "t", "archived": next(4) == 0, "links": links})));
            }
            if next(3) == 0 {
                continue; // no read: the next write starts from a kept graph or none
            }
            let links: Vec<(i64, i64, String)> = c
                .iter()
                .flat_map(|(from, e)| e.links.iter().map(move |(to, k)| (from as i64, *to, k.clone())))
                .collect();
            let fresh = build_adjacency(links.iter().map(|(a, b, k)| (*a, *b, k.as_str())), |id| {
                c.contains(id)
            });
            let visible = |id: i64| c.get(id).is_some_and(|e| !e.archived);
            let fresh_unarchived = filter_adjacency(&fresh, Some(&visible));
            assert_eq!(snapshot(&c.adjacency()), snapshot(&fresh), "step {step}");
            assert_eq!(
                snapshot(&c.unarchived_adjacency()),
                snapshot(&fresh_unarchived),
                "step {step}"
            );
        }
    }
}
