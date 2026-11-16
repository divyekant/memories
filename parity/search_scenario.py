"""Cases of the search routes with hybrid set to false (task P2-2).

The cases run on the state that the golden scenario has just before its first
search case (`usage-headers-search`): the seed corpus, the API keys and the
record of `usage-headers-add`. Hybrid search with real keyword scores is
task P2-4 (gate G3). Two hybrid cases here set `vector_weight` to 1.0, so
that the keyword side adds no score; they mask the keyword candidates.

`server/scripts/parity_search.sh` replays these cases against the Rust server.
"""

from parity.scenario import c

Q = "How is the fleet-api rate limit configured?"
GOLDEN_NAME = "search/routes.ndjson"
# The golden case before which the search cases run.
STATE_BEFORE = "usage-headers-search"
KEYWORD_KEYS = ("bm25_candidates", "candidates_considered")


def build():
    return [
        c("p22-key-ro", "POST", "/api/keys",
          json={"name": "p22-ro", "role": "read-only", "prefixes": ["claude-code/"]},
          save={"key:ro": "key"}),
        c("p22-search-vector", "POST", "/search", json={"query": Q, "k": 5, "hybrid": False}),
        c("p22-search-k100", "POST", "/search", json={"query": "release", "k": 100, "hybrid": False}),
        c("p22-search-unknown-field", "POST", "/search",
          json={"query": Q, "k": 3, "hybrid": False, "not_a_field": 1}),
        c("p22-search-prefix", "POST", "/search",
          json={"query": "incident root cause", "k": 6, "hybrid": False, "source_prefix": "claude-code/"}),
        c("p22-search-prefix-boundary", "POST", "/search",
          json={"query": "design decision", "k": 6, "hybrid": False, "source_prefix": "codex/voltis",
                "source_boundary": True}),
        c("p22-search-prefixes-boundary", "POST", "/search",
          json={"query": "design decision", "k": 6, "hybrid": False,
                "source_prefixes": ["codex/voltis", "wip/voltis"], "source_boundary": True}),
        c("p22-search-prefix-none", "POST", "/search",
          json={"query": Q, "k": 4, "hybrid": False, "source_prefix": "no-such-prefix/"}),
        c("p22-search-archived", "POST", "/search",
          json={"query": Q, "k": 10, "hybrid": False, "include_archived": True}),
        c("p22-search-threshold", "POST", "/search",
          json={"query": Q, "k": 10, "hybrid": False, "threshold": 0.4}),
        c("p22-search-temporal", "POST", "/search",
          json={"query": "what changed last month in the billing-engine", "k": 5, "hybrid": False,
                "reference_date": "2026-03-15T00:00:00Z"}),
        c("p22-search-since-until", "POST", "/search",
          json={"query": Q, "k": 5, "hybrid": False, "since": "2020-01-01", "until": "2099-01-01",
                "auto_intent": False}),
        c("p22-search-until-past", "POST", "/search",
          json={"query": Q, "k": 5, "hybrid": False, "until": "2001-01-01", "auto_intent": False}),
        c("p22-search-ro-scope", "POST", "/search", json={"query": Q, "k": 5, "hybrid": False}, key="ro"),
        c("p22-search-hybrid-vector-weight-1", "POST", "/search",
          json={"query": Q, "k": 8, "hybrid": True, "vector_weight": 1.0}),
        c("p22-search-hybrid-vector-weight-1-no-graph", "POST", "/search",
          json={"query": "who owns billing-engine", "k": 6, "hybrid": True, "vector_weight": 1.0,
                "graph_weight": 0.0, "source_prefix": "codex/"}),
        c("p22-explain", "POST", "/search/explain",
          json={"query": Q, "k": 5, "vector_weight": 1.0}, volatile=KEYWORD_KEYS),
        c("p22-explain-ro-403", "POST", "/search/explain", json={"query": Q}, key="ro"),
        c("p22-evidence", "POST", "/search/evidence", json={"query": Q, "k": 5, "hybrid": False}),
        c("p22-evidence-latest", "POST", "/search/evidence",
          json={"query": "latest billing-engine owner", "k": 4, "hybrid": False}),
        c("p22-batch", "POST", "/search/batch",
          json={"queries": [{"query": Q, "k": 3, "hybrid": False},
                            {"query": "postmortem", "k": 2, "hybrid": False, "source_prefix": "codex/"}]}),
        c("p22-batch-ro", "POST", "/search/batch",
          json={"queries": [{"query": Q, "k": 3, "hybrid": False}]}, key="ro"),
        c("p22-is-novel-dup", "POST", "/memory/is-novel", json={"text": "{{seed_text}}"}),
        c("p22-is-novel-new", "POST", "/memory/is-novel",
          json={"text": "Parity: a fact that no record holds.", "threshold": 0.9}),
        c("p22-is-novel-ro", "POST", "/memory/is-novel", json={"text": "postmortem"}, key="ro"),
    ]
