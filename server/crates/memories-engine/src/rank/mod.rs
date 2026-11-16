//! The rank core of the search (task P2-1): a port of the rank rules of the
//! Python server v5.19.0 (`memory_engine.py`, `query_intent.py`) as pure
//! functions. The golden cases in `parity/golden/rank/` check each rule.

pub mod fusion;
pub mod graph;
pub mod intent;
pub mod pydt;
pub mod pyorder;
pub mod scores;

pub use fusion::{HybridInput, MatchType, Ranked, Ranking, RecordFields, hybrid_rank};
pub use pydt::PyDateTime;
pub use scores::{WeightInput, Weights};
