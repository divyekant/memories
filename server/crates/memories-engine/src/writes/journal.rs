//! The journal of the writes of two steps: `supersede`, `merge_memories` and
//! `delete_memory`. Each write puts an entry in `state.db` before its first
//! step and removes the entry after its last step. At start, `repair`
//! completes each entry that is left, then removes it:
//!
//! - `supersede`: if the new record is stored, archive the old record with
//!   `superseded_by`. If not, there is nothing to do.
//! - `merge`: if the new record is stored, archive the merged records.
//! - `delete`: remove the links to the record, then the record.
//!
//! Each repair step is safe to do again, so a stop during `repair` is safe.

use serde_json::Value;

use super::{Result, Writer};

impl Writer {
    pub(super) fn begin(&self, op: Value) -> Result<i64> {
        Ok(self.e().state.begin_op(&op.to_string())?)
    }

    /// Only a write that completes calls `end`. After an error, the entry stays
    /// and the next start completes or drops the write.
    pub(super) fn end(&self, id: i64) -> Result<()> {
        Ok(self.e().state.end_op(id)?)
    }

    /// Completes the journal entries that a stop left. Returns their count.
    pub async fn repair(&self) -> Result<usize> {
        let ops = self.e().state.pending_ops()?;
        for (id, op) in &ops {
            let v: Value = serde_json::from_str(op).unwrap_or(Value::Null);
            let new = v["new"].as_i64();
            let stored = |id: Option<i64>| id.is_some_and(|i| self.e().catalog().contains(i));
            match v["op"].as_str() {
                Some("supersede") if stored(new) && stored(v["old"].as_i64()) => {
                    let old = self.records(&[v["old"].as_u64().unwrap_or(0)]).await?;
                    self.archive(old, new.map(|n| n as u64)).await?;
                }
                Some("merge") if stored(new) => {
                    let ids: Vec<u64> = v["ids"]
                        .as_array()
                        .into_iter()
                        .flatten()
                        .filter_map(Value::as_u64)
                        .filter(|&i| stored(Some(i as i64)))
                        .collect();
                    let records = self.records(&ids).await?;
                    self.archive(records, None).await?;
                }
                Some("delete") => {
                    let target = v["id"].as_i64().unwrap_or(-1);
                    let rec = self.record(target).await?;
                    self.delete_steps(target, rec.as_ref()).await?;
                }
                Some("supersede" | "merge") => {}
                _ => tracing::warn!(op = %op, "unknown journal entry"),
            }
            self.end(*id)?;
            tracing::info!(op = %op, "completed a write that a stop interrupted");
        }
        Ok(ops.len())
    }
}
