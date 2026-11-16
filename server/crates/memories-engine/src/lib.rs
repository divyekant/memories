//! Rank logic, search, writes, links and maintenance.

pub mod catalog;
pub mod engine;
pub mod evidence;
pub mod keyword;
pub mod rank;
pub mod records;
pub mod reinforce;
pub mod scope;
pub mod search;

pub use engine::{Engine, EngineError};
