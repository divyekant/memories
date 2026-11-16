//! The journal of the writes of two steps: `supersede`, `merge_memories` and
//! `delete_memory`. Each write puts an entry in `state.db` before its first
//! step and removes the entry after its last step. If a step fails, the write
//! completes the entry at once (`close`). At start, `repair` completes each
//! entry that a stop left, then removes it:
//!
//! - `supersede`: if the new record is stored, archive the old record with
//!   `superseded_by`. If not, there is nothing to do.
//! - `merge`: if the new record is stored, archive the merged records.
//! - `delete`: remove the links to the record, then the record.
//!
//! Each repair step is safe to do again, so a stop during `repair` is safe.

use serde_json::Value;

use super::{Result, Writer, is_pinned};

impl Writer {
    pub(super) fn begin(&self, op: &Value) -> Result<i64> {
        Ok(self.e().state.begin_op(&op.to_string())?)
    }

    /// Removes the entry after the steps of its write. After a failed step, it
    /// first completes the entry; if that also fails, the entry stays for the
    /// repair at the next start.
    pub(super) async fn close<T>(&self, entry: i64, op: &Value, done: Result<T>) -> Result<T> {
        if done.is_err()
            && let Err(e) = self.complete(op).await
        {
            tracing::warn!(error = %e, op = %op, "a failed write stays in the journal");
            return done;
        }
        self.e().state.end_op(entry)?;
        done
    }

    /// Completes the journal entries that a stop left. Returns their count.
    pub async fn repair(&self) -> Result<usize> {
        let ops = self.e().state.pending_ops()?;
        for (id, op) in &ops {
            let v: Value = serde_json::from_str(op).unwrap_or(Value::Null);
            self.complete(&v).await?;
            self.e().state.end_op(*id)?;
            tracing::info!(op = %op, "completed a write that a stop interrupted");
        }
        Ok(ops.len())
    }

    /// The steps of one entry that are not done yet.
    async fn complete(&self, v: &Value) -> Result<()> {
        let stored = |id: Option<i64>| id.is_some_and(|i| self.e().catalog().contains(i));
        let new = v["new"].as_i64();
        match v["op"].as_str() {
            Some("supersede") if stored(new) && stored(v["old"].as_i64()) => {
                let old = self.records(&[v["old"].as_u64().unwrap_or(0)]).await?;
                // A later supersede of the same record (a higher ID) wins.
                let later = old
                    .iter()
                    .any(|r| r.superseded_by.zip(new).is_some_and(|(s, n)| s > n));
                if !later {
                    self.archive(old, new.map(|n| n as u64)).await?;
                }
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
                // The delete started on a record with no pin; a later pin wins.
                if !rec.as_ref().is_some_and(is_pinned) {
                    self.delete_steps(target, rec.as_ref()).await?;
                }
            }
            Some("supersede" | "merge") => {}
            _ => tracing::warn!(op = %v, "unknown journal entry"),
        }
        Ok(())
    }
}
