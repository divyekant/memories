# Rust server: keyword index

Date: 2026-11-16
Status: adopted (task P0-2 of the Rust rewrite plan)

## Decision

The Rust server uses a Qdrant sparse vector with the IDF modifier for keyword
search. A tantivy index on disk is not necessary. It stays the fallback of gate
G3 only.

The Rust code must follow these rules:

| Item | Rule |
|---|---|
| Document tokens | `text.lower().split()`, the same as `MemoryEngine._rebuild_bm25_now`. No stopword removal. |
| Query tokens | The rule of `MemoryEngine._bm25_query_tokens`: lowercase, split on white space, strip `?` at the end, remove the stopwords. If no token stays, use all the tokens. |
| Token index | FNV-1a 32-bit hash of the UTF-8 bytes of the token. If two tokens of one document have the same hash, add their weights. |
| Document weight | `tf * (k1 + 1) / (tf + k1 * (1 - b + b * dl / avgdl))`, with `k1` 1.5 and `b` 0.75. |
| Query weight | The count of the token in the query. rank_bm25 also adds a repeated query term two times. |
| IDF | Qdrant calculates it (modifier `idf`) over the full collection. rank_bm25 also uses all records, archived records included. |
| Order | Sort the Qdrant hits by score (high first), then by record ID (low first). Qdrant gives tied hits in a different order on each run. Python gives them in corpus order. |
| `avgdl` | Keep the sum of the document lengths and the record count in `state.db`. An add adds the length, a delete subtracts it, and an update replaces the old length with the new one. Write each document vector with the current average at that time. Write all the vectors again in a background job when all of these are true: the store has 1,000 records or more, the current average is more than 25% from the value of the last full pass, and the last full pass was 1 hour ago or more. Also start the job, with no time limit, when the current average is more than 2 times or less than half of the value of the last pass (task P3-4). |

## The full pass (task P3-4)

The job is `Writer::refresh_keywords` in
`server/crates/memories-engine/src/writes/refresh.rs`. The server checks the
rule each 60 seconds.

| Item | Rule |
|---|---|
| Start | The rule of `avgdl` above. If no pass is recorded, a store of 1,000 records or more starts a pass. A pass that did not complete always continues. |
| Time limit, 2 times rule | Gate G3 finding 2: with the 1 hour limit only, a store that grows fast (for example, an import of long records into a store of short records) keeps a stale average for up to 1 hour. When the average is more than 2 times or less than half of the last value, the overlap with rank_bm25 is 0.90 or less (results of P0-2), so the pass starts at once. |
| Batches | 100 records in ID order. The job holds the write lock for one batch: it reads the records, writes their keyword vectors (Qdrant `update_vectors`) and moves the cursor. An add waits for one batch at most. |
| Average | The current average at each batch. The average of the last batch is the value of the pass. If the average at the last batch is more than 25% from the average at the first batch (a large import during the pass), the pass starts again from the first record. |
| What changes | Only the keyword vector of each point. The dense vector and the payload stay. |
| State | `state.db`, table `meta`: `keyword_pass_avgdl`, `keyword_pass_at` (Unix seconds) and `keyword_pass_cursor` (the next record ID of a pass that runs). |
| Forced stop | Each record keeps a correct vector of the old or of the new average. The next start continues from the cursor. A stop between a batch write and the cursor write makes the next pass write that batch again, with no harm. |
| One pass at a time | A second call returns at once. |
| Migration | `migrate` writes each vector with the exact average, so it records a pass. |
| Restore | A restore clears the value of the last pass and stops a running pass. The next check starts a new pass if the store has 1,000 records or more. |

Two environment variables are for tests only:
`MEMORIES_KEYWORD_REFRESH_CHECK_SEC` (default 60) and
`MEMORIES_KEYWORD_REFRESH_MIN_GAP_SEC` (default 3600, the 1 hour limit).

The reference code of these rules is `parity/keyword_index/bm25_sparse.py`.
`tests/test_keyword_index.py` checks it against `memory_engine.py` and against
`rank_bm25`.

## Test

The script `parity/keyword_index/run.py` builds rank_bm25 (the Python server)
and a Qdrant collection from the same documents. Then it compares the two
result lists for each query. Each Qdrant collection has one sparse vector for
each `avgdl` rule:

| Variant | `avgdl` | `b` |
|---|---|---|
| `exact` | The average of the corpus, as rank_bm25 calculates it | 0.75 |
| `avgdl_x0.5` | Half of the average (an old value after the corpus became longer) | 0.75 |
| `avgdl_x2` | Two times the average (an old value after the corpus became shorter) | 0.75 |
| `b0` | Not used: no length part | 0 |
| `text_bm25` | Qdrant 1.20 only: BM25 over a full-text payload index. Qdrant keeps the document lengths. | 0.75 |

Data:

- The seed corpus of the parity kit (239 records) with 200 queries from a fixed
  random seed.
- LongMemEval S (cleaned): 500 questions. Each question has its own collection
  of its chunks (about 230), the same chunks that `eval/longmemeval.py` stores.
  Hybrid R@5 uses each fifth question (100 questions), because the dense side
  costs about 30 s for each question on 4 CPUs.
- A scale test: 66,000 LongMemEval chunks in one collection (about the
  production record count) and 100 questions.

Metrics:

- Overlap at 10: the part of the first 10 rank_bm25 results that is also in the
  first 10 Qdrant results. The tie-aware value also counts a Qdrant result with
  the same rank_bm25 score as the tenth result.
- Top 1: the first result is the same. The tie-aware value counts a first result
  with the same rank_bm25 score.
- R@5: the session recall of `LongMemEvalRunner.compute_recall_at_k`, for the
  keyword list alone and for hybrid search. Hybrid search uses the fusion of
  `MemoryEngine.hybrid_search` (vector weight 0.7, RRF k 60, `k` 50, 3 times
  oversample) with no recency, feedback or graph weight. The dense list is an
  exact cosine search with all-MiniLM-L6-v2, the same for each variant.

Raw numbers: `parity/keyword_index/results-2026-11-16.json`. The file is the
output of steps 2 and 3 of "How to run the test again", merged by hand, plus
the date and the Qdrant versions.

## Results

LongMemEval, 500 questions (hybrid R@5 on 100):

| Keyword list | Overlap at 10 | Top 1 | Keyword R@5 | Hybrid R@5 |
|---|---|---|---|---|
| rank_bm25 (Python) | 1.000 | 1.000 | 0.952 | 0.960 |
| 1.15.4 `exact` | 0.954 | 0.960 | 0.950 | 0.960 |
| 1.15.4 `avgdl_x0.5` | 0.903 | 0.860 | 0.944 | 0.970 |
| 1.15.4 `avgdl_x2` | 0.919 | 0.896 | 0.956 | 0.960 |
| 1.15.4 `b0` | 0.869 | 0.770 | 0.948 | 0.970 |
| 1.20.1 `exact` | 0.954 | 0.960 | 0.950 | 0.960 |
| 1.20.1 `text_bm25` | 0.937 | 0.942 | 0.950 | 0.960 |

On LongMemEval, the tie-aware values are equal to the plain values (to 0.001),
because ties are rare in long chunks.

Seed corpus, 200 queries, Qdrant 1.15.4. On 1.20.1, the tie-aware values are
the same, except `b0` (0.940 and 0.820). `text_bm25` gives 1.000 and 1.000. The plain values change on each run,
because the tie order changes.

| Variant | Overlap at 10 | Tie-aware overlap at 10 | Top 1 | Tie-aware top 1 |
|---|---|---|---|---|
| `exact` | 0.989 | 1.000 | 0.565 | 1.000 |
| `avgdl_x0.5` | 0.987 | 0.999 | 0.570 | 1.000 |
| `avgdl_x2` | 0.987 | 0.998 | 0.565 | 1.000 |
| `b0` | 0.929 | 0.949 | 0.475 | 0.850 |

The seed corpus has many records from the same templates, so many scores are
equal. All the differences of `exact` on the seed corpus are tie order.

Scale test, 66,000 records, 100 queries, 150 results:

| Index | p50 | p95 |
|---|---|---|
| rank_bm25 `get_scores` (the Python server, no cache) | 144 ms | 340 ms |
| Qdrant 1.15.4 sparse `exact` | 12 ms | 19 ms |
| Qdrant 1.20.1 sparse `exact` | 12 ms | 16 ms |
| Qdrant 1.20.1 `text_bm25` | 2.4 ms | 3.5 ms |

FNV-1a 32-bit collisions: 27 pairs in 397,115 distinct tokens at 66,000
records. On LongMemEval, 18 pairs in all 500 questions. The two tokens of a
pair share one IDF. This has no effect on the numbers above.

## Why

- With the exact `avgdl`, the Qdrant ranks are almost the same as rank_bm25:
  0.954 overlap at 10 and the same first result for 96% of the questions. The
  remaining difference comes from the IDF formulas. rank_bm25 uses
  `ln((N - n + 0.5) / (n + 0.5))` with a floor for terms in more than half of
  the documents. Qdrant uses `ln(1 + (N - n + 0.5) / (n + 0.5))`.
- Keyword R@5 and hybrid R@5 do not change with any variant. The differences
  are 4 questions of 500 or less for keyword R@5, and 1 question of 100 for
  hybrid R@5. Gate G3 can pass with the sparse vector.
- An old `avgdl` value (half or two times) moves the ranks more than the IDF
  difference. It does not change R@5. `b0` moves the ranks most, so the length
  part stays.
- The test did not measure the 25% limit, and it did not measure a collection
  with a mix of vectors from different `avgdl` values. The test measured only
  collections where all the vectors use one old value. The 25% limit is
  between `exact` and the two measured old values. Task P2-4 (gate G3) checks
  the rule on a store that grows. The record-count and time limits stop a new
  or small store, and a bulk import, from causing one full pass after each
  write.
- The sparse vector is one write for each record, in the same Qdrant point as
  the dense vector. Nothing grows in the process memory with the record count.
  A tantivy index needs a second store, its own backup and its own crash
  recovery. The test found no reason for that cost.
- Qdrant has the IDF modifier from 1.10, so Qdrant 1.15.4 of each current
  install is enough.

## Differences from the Python server

These differences are in the Rust server. G3 measures their effect.

1. The keyword scores are different (the IDF formula). The ranks are near the
   same, but not equal.
2. Tied hits come in record ID order. Python uses the corpus order, which is
   also the ID order after a start. If more tied hits exist than the limit,
   Qdrant selects which tied hits are in the list.
3. A new record is in keyword search immediately. Python adds it after
   `MEMORIES_PERSIST_DELAY_SEC`.
4. Two tokens with the same FNV-1a hash share one dimension.
5. If a term is in exactly half of the documents, rank_bm25 gives it IDF 0. A
   document that has only that term then has score 0, and Python drops it.
   The Qdrant IDF is always more than 0, so the Rust server returns that
   document.

## Newer Qdrant releases

The newest stable Qdrant release on 2026-11-16 is 1.20.1 (2026-11-13). The
sparse vector test gives the same `exact` numbers on 1.20.1 as on 1.15.4 (to
0.001 on LongMemEval). The other variants differ by 0.01 or less. Qdrant 1.20
adds two features for this search:

| Feature | Gain | Why the Rust server does not use it now |
|---|---|---|
| BM25 over a full-text payload index (`text_bm25`, query `{"text": ...}`, with `k1` and `b` for each query and an IDF corpus filter) | Qdrant keeps the document lengths, so no `avgdl` value and no background pass. Query time 2.4 ms in place of 12 ms at 66,000 records. | Needs Qdrant 1.20. Its overlap at 10 is 0.937, not 0.954. The cause is not known: the `whitespace` tokenizer, the IDF and the `avgdl` are expected to be the same as `exact`. The query terms or a token length limit can be the cause. |
| Weights for the RRF fusion of the query API (`rrf.weights`) | The fusion of `hybrid_search` (0.7 and 0.3) can run in Qdrant, in one request. | Needs Qdrant 1.20. The recency, feedback and confidence signals of `hybrid_search` still need fusion in the Rust code. The test did not check that the Qdrant RRF formula is the same as the Python formula. |

To get 1.20, an install changes the Qdrant image tag in its compose file (for
example `qdrant/qdrant:v1.20.1`) and starts the stack again. Read the Qdrant
upgrade notes before this change: Qdrant can limit an upgrade that skips minor
versions. Make a backup of the Qdrant volume first.

After v6.0.0, the Rust server can use `text_bm25` when the Qdrant server is
1.20 or newer. Make that a separate task with its own G3 run.

## How to run the test again

1. Start two Qdrant servers:

   ```bash
   docker run -d -p 16333:6333 qdrant/qdrant:v1.15.4
   docker run -d -p 16433:6333 qdrant/qdrant:v1.20.1
   ```

2. Run the comparison (about 20 minutes on 4 CPUs; the first run also gets the
   LongMemEval data and the embedding model):

   ```bash
   .venv/bin/python -m parity.keyword_index.run \
       --qdrant 1.15.4=http://127.0.0.1:16333 \
       --qdrant 1.20.1=http://127.0.0.1:16433 \
       --out /tmp/p0-2.json
   ```

3. Run the scale test:

   ```bash
   .venv/bin/python -m parity.keyword_index.run --dataset scale \
       --qdrant 1.15.4=http://127.0.0.1:16333 \
       --qdrant 1.20.1=http://127.0.0.1:16433 \
       --out /tmp/p0-2-scale.json
   ```
