//! The engine handle of the server: the store, `state.db`, the embedder, the
//! catalog (source map and link map) and the reinforcement buffer.

use std::path::Path;
use std::sync::{RwLock, RwLockReadGuard, RwLockWriteGuard};

use memories_embed::{EmbedConfig, EmbedError, Embedder};
use memories_store::{QdrantSettings, QdrantStore, StateDb, StateError, StoreError};
use memories_types::Record;

use crate::catalog::Catalog;
use crate::rank::GraphParams;
use crate::rank::intent::IntentError;
use crate::reinforce::Reinforcements;

#[derive(Debug, thiserror::Error)]
pub enum EngineError {
    #[error(transparent)]
    Store(#[from] StoreError),
    #[error(transparent)]
    State(#[from] StateError),
    #[error("embedder: {0}")]
    Embed(#[from] EmbedError),
    #[error("query intent: {0:?}")]
    Intent(IntentError),
    #[error("{0}")]
    Config(String),
}

/// Records in one scroll page when the catalog loads.
const CATALOG_PAGE: u32 = 1_000;

pub struct Engine {
    pub store: QdrantStore,
    pub state: StateDb,
    pub embedder: Embedder,
    pub catalog: RwLock<Catalog>,
    pub reinforce: Reinforcements,
    pub graph: GraphParams,
    /// The dimension of the dense vector (of the embedder).
    pub dim: u64,
}

impl Engine {
    /// Opens `state.db` in `data_dir`, loads the embedder, makes the
    /// collection if it does not exist, and loads the catalog.
    pub async fn open(
        settings: &QdrantSettings,
        data_dir: &Path,
        embed: EmbedConfig,
        graph: GraphParams,
    ) -> Result<Self, EngineError> {
        let state = StateDb::open(&data_dir.join("state.db"))?;
        let embedder = Embedder::load(embed).await?;
        let dim = embedder.dim() as u64;
        let store = QdrantStore::new(settings, &settings.v2_collection())?;
        store.ensure_collection(dim).await?;
        let engine = Self {
            store,
            state,
            embedder,
            catalog: RwLock::new(Catalog::default()),
            reinforce: Reinforcements::default(),
            graph,
            dim,
        };
        engine.load_catalog().await?;
        Ok(engine)
    }

    /// Reads all records into a new catalog, in pages.
    pub async fn load_catalog(&self) -> Result<usize, EngineError> {
        let mut catalog = Catalog::default();
        let mut offset = None;
        loop {
            let (records, next) = self.store.scroll(None, offset, CATALOG_PAGE).await?;
            for r in &records {
                catalog.put(r);
            }
            match next {
                Some(n) => offset = Some(n),
                None => break,
            }
        }
        let n = catalog.len();
        *self.catalog_mut() = catalog;
        Ok(n)
    }

    pub fn catalog(&self) -> RwLockReadGuard<'_, Catalog> {
        self.catalog.read().unwrap_or_else(|e| e.into_inner())
    }

    pub fn catalog_mut(&self) -> RwLockWriteGuard<'_, Catalog> {
        self.catalog.write().unwrap_or_else(|e| e.into_inner())
    }

    /// The record count (`len(self.metadata)`).
    pub fn count(&self) -> usize {
        self.catalog().len()
    }

    /// Shows a pending reinforcement on a record that a read got from the store.
    pub fn overlay(&self, record: &mut Record) {
        self.reinforce.overlay(record);
    }

    /// Writes the pending reinforcements to the store.
    pub async fn flush_reinforcements(&self) -> Result<usize, EngineError> {
        Ok(self.reinforce.flush(&self.store).await?)
    }
}

/// The graph settings from `SEARCH_PPR_ALPHA`, `SEARCH_PPR_MAX_ITERS`,
/// `SEARCH_PPR_MIN_RELATIVE` and `SEARCH_GRAPH_RESERVED_SLOTS` (else
/// `SEARCH_GRAPH_MAX_NEIGHBORS`). Python `int()` and `float()` fail at start
/// on a bad value, so this function refuses it too.
pub fn graph_params(get: impl Fn(&str) -> Option<String>) -> Result<GraphParams, EngineError> {
    fn num<T: std::str::FromStr>(
        name: &str,
        raw: Option<String>,
        default: T,
    ) -> Result<T, EngineError> {
        match raw {
            None => Ok(default),
            Some(v) => v.trim().parse().map_err(|_| {
                EngineError::Config(format!(
                    "{name}={v:?} is not a valid number. Set a number or remove it."
                ))
            }),
        }
    }
    let d = GraphParams::default();
    let slots_name = if get("SEARCH_GRAPH_RESERVED_SLOTS").is_some() {
        "SEARCH_GRAPH_RESERVED_SLOTS"
    } else {
        "SEARCH_GRAPH_MAX_NEIGHBORS"
    };
    let slots: i64 = num(slots_name, get(slots_name), d.reserved_slots as i64)?;
    let iters: i64 = num(
        "SEARCH_PPR_MAX_ITERS",
        get("SEARCH_PPR_MAX_ITERS"),
        d.max_iters as i64,
    )?;
    Ok(GraphParams {
        alpha: num("SEARCH_PPR_ALPHA", get("SEARCH_PPR_ALPHA"), d.alpha)?,
        // Python range() and slicing treat a negative value as 0.
        max_iters: iters.max(0) as usize,
        min_relative: num(
            "SEARCH_PPR_MIN_RELATIVE",
            get("SEARCH_PPR_MIN_RELATIVE"),
            d.min_relative,
        )?,
        reserved_slots: slots.max(0) as usize,
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn graph_params_read_python_names() {
        let env = |pairs: &'static [(&'static str, &'static str)]| {
            move |k: &str| pairs.iter().find(|p| p.0 == k).map(|p| p.1.to_string())
        };
        assert_eq!(graph_params(env(&[])).unwrap(), GraphParams::default());
        let p = graph_params(env(&[
            ("SEARCH_GRAPH_MAX_NEIGHBORS", "4"),
            ("SEARCH_PPR_ALPHA", " 0.5"),
        ]))
        .unwrap();
        assert_eq!((p.reserved_slots, p.alpha), (4, 0.5));
        let p = graph_params(env(&[
            ("SEARCH_GRAPH_MAX_NEIGHBORS", "4"),
            ("SEARCH_GRAPH_RESERVED_SLOTS", "1"),
        ]))
        .unwrap();
        assert_eq!(p.reserved_slots, 1);
        assert!(graph_params(env(&[("SEARCH_PPR_MAX_ITERS", "3.5")])).is_err());
    }
}
