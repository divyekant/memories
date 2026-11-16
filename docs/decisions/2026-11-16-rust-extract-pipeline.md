# Rust server: extraction pipeline, commit route and profiles

Date: 2026-11-16
Status: adopted (task P4-3 of the Rust rewrite plan)

## Scope

The Rust server runs the extraction pipeline of `llm_extract.run_extraction`
(Python v5.19.0) for each queued job:

1. Transcript hygiene (`clean_transcript`), then secret redaction
   (`redact_secrets`, off when `EXTRACT_REDACT_SECRETS` is `0`, `false` or `no`).
2. Stage 1: fact extraction. The prompts are the Python texts, byte for byte.
   The profile mode `aggressive` uses the `pre_compact` prompt.
3. Stage 2: the AUDN decision (add, update, delete, conflict, noop). Each fact
   gets a hybrid search with `k = EXTRACT_SIMILAR_PER_FACT` (5). If the LLM
   call or its reply fails, each fact becomes a fallback add.
4. Stage 3: the writes. An add has the novelty gate (0.85). An update archives
   the old record, adds the new one with `supersedes`, and links the two. A
   conflict adds the record with `conflicts_with`.
5. The auto `related_to` links (at most 3, minimum score 0.005, same source)
   and the compaction candidates.
6. `dry_run`, `debug`, the profile `single_call` mode, the regex fallback and
   the 3 JSON parse rules.

The new routes:

| Route | Behavior |
|---|---|
| `POST /memory/extract/commit` | Runs the approved actions of a dry run with no novelty gate. A project policy error gives 422. |
| `GET /extraction/profiles` | Lists the profiles. Admin only. |
| `GET`, `PUT`, `DELETE /extraction/profiles/{prefix}` | One profile. Admin only. `PUT` writes the audit row `extraction.profile_updated`. |

The profiles are in `<DATA_DIR>/extraction_profiles.json`, the Python file
and format. `POST /memory/extract` resolves the profile when it writes the
job, as Python does, and stores it in the job request.

With no provider and `EXTRACT_FALLBACK_ADD=true`, `POST /memory/extract` runs
the regex fallback in the request, as Python does. The job is stored as
finished.

The job results keep the Python keys that the MCP server, the web UI and the
usage rows of task P5-1 read: `actions`, the counts, `links_created`,
`tokens.extract` and `tokens.audn`.

## Exit check

| Check | Result |
|---|---|
| `parity/extract_golden.py --check`: 1,321 cases of hygiene, redaction, fallback lines, parse rules, prompts, fact and decision parsing, single-call and profiles against the Python functions | Rust tests (`tests/extract_text.rs`) match each case |
| `server/scripts/parity_pipeline.sh`: 16 live cases with recorded LLM replies (a fake OpenAI chat server), against `parity/golden/extract/pipeline.ndjson` from Python | 0 differences. Each prompt is equal byte for byte. Each decision, job result and stored record is equal. |

The live cases cover each AUDN action, a pinned record, the novelty gate,
`pre_compact` with hygiene and secrets, an empty transcript after hygiene, an
extraction error with the runtime fallback, an AUDN error, a prose reply, the
dry run and the commit, the debug trace, two profiles (single call, aggressive
with rules and limits), a single-call reply that is not valid, a project policy
error, a scoped key, and the profile routes.

The driver uses the OpenAI provider, because the parity environment has the
`openai` package and not the `anthropic` package. The pipeline is the same for
each provider.

## Differences from Python

| Case | Python | Rust | Reason |
|---|---|---|---|
| Commit or sync fallback with an admin managed key | The key's empty prefix list refuses each write | No source limit | The same rule as the queued path (`allowed_prefixes` is `None` for admin) |
| Single-call provider error | The job fails at once | The try fails; the job gets 3 tries | The queue retry rule of task P4-1 |
| Store not ready, or an error in the regex fallback, during a job | The job fails at once | The try fails; the job gets 3 tries | The same retry rule |
| A panic in a job | Any exception fails the job at once | The try fails; the job gets 3 tries | The same retry rule |
| Provider error text in `error_message` and `primary_error` | The SDK exception text | The Rust provider error text | Different HTTP clients. The parity check masks these keys. |
| A `fact_index` or `old_id` that is not an integer | Python type errors of different kinds | A float `fact_index` or a string fails the job; an integral float `old_id` finds the record | Edge cases of LLM replies |
| Project promotion | Gated by the promotion settings | Not run | Promotion is off in production. Task P5-6 owns it. |
| Shadow providers, `EXTRACT_TRAINING_DATA_DIR`, `memory_trimmer` | Present | Not ported | Not used in production |
| A job with no store yet | Not possible | A job that needs no write ends; a job that needs a write waits for a retry | The store can open after the first-start migration |

A store write error inside one action gives an `error` action, as in
Python. The job does not fail.

A job can run again after a forced stop or a panic. The new run calls the LLM
again and does all the writes again. Only some writes are safe to repeat:

| Action | Second run |
|---|---|
| ADD | Safe: the add is deduplicated |
| UPDATE with an `old_id` | Safe: the old record is archived, so the update skips it |
| UPDATE with a null `old_id` | Makes a duplicate record |
| CONFLICT | Makes a second conflict record |
| DELETE | Gives an `error` action, because the record is gone |

A profile `max_facts` or `max_fact_chars` that is negative or a float uses
the default. Python slices with the negative value, or fails on the float.

`EXTRACT_MAX_FACTS` stays "not used": the profile always gives `max_facts`.

## Settings

These settings now have an effect: `EXTRACT_FALLBACK_ADD`,
`EXTRACT_FALLBACK_MAX_FACTS`, `EXTRACT_FALLBACK_MIN_FACT_CHARS`,
`EXTRACT_FALLBACK_MAX_FACT_CHARS`, `EXTRACT_FALLBACK_NOVELTY_THRESHOLD`,
`EXTRACT_MAX_FACT_CHARS`, `EXTRACT_MAX_LINKS`, `EXTRACT_MIN_LINK_SCORE`,
`EXTRACT_NOVELTY_GATE`, `EXTRACT_NOVELTY_THRESHOLD`, `EXTRACT_REDACT_SECRETS`,
`EXTRACT_SIMILAR_PER_FACT` and `EXTRACT_SIMILAR_TEXT_CHARS`.
