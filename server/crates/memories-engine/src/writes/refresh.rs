//! The full pass of the keyword vectors (decision P0-2, task P3-4). Each write
//! gives its records a keyword vector with the average document length of
//! that time. When the average moves far from the value of the last pass, the
//! pass writes all the keyword vectors again with the current average.

use std::time::Duration;

use memories_store::KeywordPass;

use super::{Result, Writer, avgdl, text_of};
use crate::keyword::doc_vector;

/// When a pass starts, and its batch size.
#[derive(Clone, Debug)]
pub struct RefreshRule {
    /// No pass on a store with fewer records.
    pub min_records: u64,
    /// A pass when the average moves more than this part from the last pass...
    pub drift: f64,
    /// ...and the last pass ended this long ago or more.
    pub min_gap: Duration,
    /// Records in one batch. The write lock is held for one batch only.
    pub batch: u32,
}

impl Default for RefreshRule {
    fn default() -> Self {
        Self {
            min_records: 1_000,
            drift: 0.25,
            min_gap: Duration::from_secs(3_600),
            batch: 100,
        }
    }
}

impl RefreshRule {
    /// True if a pass must run now. A pass that did not complete always
    /// continues. If the average is more than 2 times or less than half of
    /// the last value, `min_gap` does not apply.
    pub fn due(&self, count: u64, avg: f64, last: &KeywordPass, now: u64) -> bool {
        if last.cursor.is_some() {
            return true;
        }
        if count < self.min_records {
            return false;
        }
        let Some(prev) = last.avgdl.filter(|p| *p > 0.0) else {
            return true;
        };
        let ratio = avg / prev;
        if !(0.5..=2.0).contains(&ratio) {
            return true;
        }
        let gap_ok = last
            .at
            .is_none_or(|at| now.saturating_sub(at) >= self.min_gap.as_secs());
        (ratio - 1.0).abs() > self.drift && gap_ok
    }
}

/// The result of a completed pass.
#[derive(Clone, Debug, PartialEq)]
pub struct PassReport {
    pub records: u64,
    pub batches: u64,
    /// The average of the last batch, recorded as the value of the pass.
    pub avgdl: f64,
}

fn unix_now() -> u64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map_or(0, |d| d.as_secs())
}

impl Writer {
    /// True if [`Self::refresh_keywords`] must run now.
    pub fn keyword_refresh_due(&self, rule: &RefreshRule) -> Result<bool> {
        let st = &self.e().state;
        let (sum, count) = st.doc_lengths()?;
        let avg = avgdl(sum as i64, count as i64);
        Ok(rule.due(count, avg, &st.keyword_pass()?, unix_now()))
    }

    /// Writes the keyword vector of each record again with the current
    /// average, in ID order, one batch under each hold of the write lock.
    /// Continues a pass that did not complete. Returns `None` if another pass
    /// runs, or if a restore stopped this pass.
    ///
    /// A stop at any point leaves each record with a correct vector (of the
    /// old or of the new average). The cursor in `state.db` moves after each
    /// batch, so the next pass continues from the last batch.
    pub async fn refresh_keywords(&self, rule: &RefreshRule) -> Result<Option<PassReport>> {
        let Ok(_one) = self.pass.try_lock() else {
            return Ok(None);
        };
        let st = &self.e().state;
        if st.keyword_pass()?.cursor.is_none() {
            st.set_keyword_cursor(0)?;
        }
        let (mut records, mut batches) = (0u64, 0u64);
        let mut first_avg: Option<f64> = None;
        loop {
            let _g = self.lock.lock().await;
            // A restore clears the cursor (`reload`): the pass stops.
            let Some(cursor) = st.keyword_pass()?.cursor else {
                return Ok(None);
            };
            let (page, next) = self
                .e()
                .store
                .scroll(None, Some(cursor), rule.batch.max(1))
                .await?;
            let (sum, count) = st.doc_lengths()?;
            let avg = avgdl(sum as i64, count as i64);
            let start_avg = *first_avg.get_or_insert(avg);
            records += page.len() as u64;
            batches += 1;
            let points = page
                .iter()
                .map(|r| (r.id, doc_vector(&text_of(r).unwrap_or_default(), avg)))
                .collect();
            self.e().store.update_keyword_vectors(points).await?;
            match next {
                Some(n) => st.set_keyword_cursor(n)?,
                // The average moved more than `drift` during the pass (a
                // large import): the first batches are stale, so start again.
                None if (avg / start_avg - 1.0).abs() > rule.drift => {
                    st.set_keyword_cursor(0)?;
                    first_avg = None;
                }
                None => {
                    st.finish_keyword_pass(avg, unix_now())?;
                    return Ok(Some(PassReport {
                        records,
                        batches,
                        avgdl: avg,
                    }));
                }
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn pass(avgdl: Option<f64>, at: Option<u64>, cursor: Option<u64>) -> KeywordPass {
        KeywordPass { avgdl, at, cursor }
    }

    #[test]
    fn due_follows_the_rule_of_decision_p0_2() {
        let r = RefreshRule::default();
        let now = 10_000;
        let old = Some(now - 3_600);
        let recent = Some(now - 60);
        // Too few records, even with no pass.
        assert!(!r.due(999, 50.0, &pass(None, None, None), now));
        // No pass yet: start one.
        assert!(r.due(1_000, 50.0, &pass(None, None, None), now));
        // 25% or less: no pass.
        assert!(!r.due(5_000, 125.0, &pass(Some(100.0), old, None), now));
        assert!(!r.due(5_000, 75.0, &pass(Some(100.0), old, None), now));
        // More than 25%, and the last pass 1 hour ago or more.
        assert!(r.due(5_000, 126.0, &pass(Some(100.0), old, None), now));
        assert!(r.due(5_000, 74.0, &pass(Some(100.0), old, None), now));
        // More than 25%, but the last pass was less than 1 hour ago.
        assert!(!r.due(5_000, 190.0, &pass(Some(100.0), recent, None), now));
        assert!(!r.due(5_000, 51.0, &pass(Some(100.0), recent, None), now));
        // More than 2 times or less than half: no time limit.
        assert!(r.due(5_000, 201.0, &pass(Some(100.0), recent, None), now));
        assert!(r.due(5_000, 49.0, &pass(Some(100.0), recent, None), now));
        // A pass that did not complete continues.
        assert!(r.due(10, 100.0, &pass(Some(100.0), recent, Some(7)), now));
    }
}
