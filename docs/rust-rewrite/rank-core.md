# Rank core (task P2-1)

The rank core is the module `rank` of the crate `memories-engine`. It holds
the rank rules of the Python server v5.19.0 as pure functions with no input
or output. Task P2-2 (search routes) calls them.

## Modules

| Module | Python source | Content |
|---|---|---|
| `fusion` | `hybrid_search`, `_merge_graph_results` | Reciprocal rank fusion (k = 60, ranks from 0), the 5 signals, the threshold, the temporal filter, the 2 places for graph results |
| `graph` | `_build_adjacency`, `_filter_adjacency`, `_graph_expand`, `_trace_top_contributors` | Personalized PageRank: alpha 0.85, 3 iterations, minimum relative score 0.05, cap 0.33 times the top score, `related_to` links only, no direction |
| `scores` | `_recency_score`, `compute_confidence`, `_passes_temporal_filter`, `annotate_relative_scores`, `_bm25_query_tokens` | Scores, weights, tokens, Python `round()` |
| `intent` | `query_intent.py` | `detect_temporal_intent` and `classify_query` |
| `pydt` | Python 3.11 `datetime` (C module) | `fromisoformat`, compare, subtract |
| `pyorder` | CPython `dict` and `set` | Insertion order, and the table order of a set of ints |

## Parity check

`parity/rank_golden.py` calls the Python functions with fixed inputs and
writes `parity/golden/rank/*.json`: 319 hybrid search cases (16 by hand, 3
with 160 records and k above 100, 300 random, 101 of them with other graph
settings), 910 intent cases, 4,339 `fromisoformat` strings and the cases of
each score rule. The set order cases include sets of up to 70,000 keys. The
Rust test `tests/rank_golden.rs` passes each case with
equal IDs in equal order and equal scores (bit for bit, which is stronger
than 6 decimals). CI runs the script with `--check`.

A branch coverage run (coverage.py, `--branch`) of the script over the Python
rank functions reaches each branch, except these:

| Python branch | Why no case |
|---|---|
| `annotate_relative_scores`: `except (TypeError, ValueError)` | A score that is not a number. The Rust types do not allow it. |
| `_related_adjacency`: cache hit | A cache, not a rank rule. |
| `_filter_adjacency`, `hybrid_search._visible`: `except (ValueError, IndexError)` | A record deleted while the search runs. In Rust, the lookup gives `None` and the record drops out, as in Python. |
| `detect_temporal_intent`: the fall-through after each `if period == ...` chain, and `else: return None` of "past N" | Not reachable: the regex group is always one of the listed words. |

To make the set order matter, a test run with sorted sets in place of the
CPython order failed 7 of the hybrid cases. Thus the golden cases check the
order of tied graph results.

## What the caller (P2-2) must do

1. For `/search`, set `last_reinforced_at` of each vector hit to the request
   time before `hybrid_rank`. The Python vector search reinforces its hits
   before the fusion, so the confidence signal sees them as new. For
   `/search/explain`, do not.
2. Ask Qdrant for `candidate_counts(k, count).vector` vector hits, which is
   at most 100, with the scope filter and the threshold in the query. Then
   apply the temporal filter to these hits, as Python does after the limit.
   Give the hits in rank order, with the similarity to 6 decimals.
3. Give the keyword hits in index order. Python sorts them by score with a
   stable sort, so a tie keeps the index order (the order of the stored
   records).
4. Build the graph with `build_adjacency` from the links in the order of the
   stored records. The order of a tie between graph results depends on it.
5. Give `visible` only when a scope applies (a source filter, or archived
   records left out). It must match the scope test of `_visible`.
6. Call `relative_scores` after the key scope filter, on `similarity`, else
   `rrf_score`.
7. Read the graph settings into `GraphParams` from `SEARCH_PPR_ALPHA`,
   `SEARCH_PPR_MAX_ITERS`, `SEARCH_PPR_MIN_RELATIVE` and
   `SEARCH_GRAPH_RESERVED_SLOTS` (else `SEARCH_GRAPH_MAX_NEIGHBORS`), with the
   Python defaults.
8. Call `classify_query` only when `auto_intent` is true. Map `IntentError`
   to HTTP 500, as Python does (for example "1000000000 days ago").

## Contract differences

- With `confidence_weight` above 0, Python `/search` ranks the vector hits by
  the microsecond of their reinforcement, which changes from run to run. The
  Rust server uses one time for each request, so a tie keeps the vector
  order. The golden cases use a fixed clock, where the two agree.
- Python reads the clock again for each recency and confidence score. Two
  records with the same time stamp can thus get scores that differ by about
  1 microsecond of age, and the tie order changes from run to run. The Rust
  server uses one time for each request.
- `/search/explain` (`hybrid_search_explain`) ranks the keyword side over the
  index positions and does not check that a keyword hit still exists. A
  record that is deleted while the index is stale thus keeps its rank place
  in Python. In Rust, it drops out. This is only possible during a race.

## Limits

- The date fields of a record must be strings. Python gives HTTP 500 for a
  number in `document_at`, `created_at` or `timestamp` when a temporal filter
  applies.
- Python 3.11 uses Unicode 14.0. Rust and the `regex` crate use a newer
  version. For a character that is new after Unicode 14.0, the lower case,
  the word boundary and `\d` can differ (for example `"yesterday\U00011F04"`
  gives the yesterday intent in Python only). The golden cases use no such
  character.
- `pow` comes from the C library on both sides. The golden cases pass on the
  glibc of the CI runner. A different C library can change the last bit of a
  recency score and thus the order of a tie.
