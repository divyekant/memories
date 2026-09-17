# Jev production shadow experiment

Jev evaluates the same facts and candidate memories as the primary model after a
successful AUDN call. AUDN selects ADD, UPDATE, DELETE, NOOP, or CONFLICT. Only the
primary model's output reaches memory mutation code. Jev never handles extraction,
single-call extraction, replacement-text generation, or production writes.

The adapter calls the [direct TypeSafe API](https://docs.typesafe.ai/api), using
`jev-latest`. It records the returned model identifier because an alias can change.
No Gateway or extra Haiku call is involved. Shadow mode compares actual production
decisions against Jev on the same input at the time of the request.

## Enable and stop

Keep `SHADOW_PROVIDERS` unset by default. To enable this experiment, put the direct
credential in the production service's private environment and set:

```dotenv
SHADOW_PROVIDERS=jev:jev-latest
TYPESAFE_API_KEY=<private credential>
SHADOW_LOG_DIR=/data/shadow-logs
```

Keep the environment file mode `0600`. Do not place credentials in source or shell
arguments. Recreate only the Memories service to apply environment changes. Remove
`jev:jev-latest` from `SHADOW_PROVIDERS` and recreate that service to stop capture.
Retain any other shadow providers. There is no automatic promotion to a primary model.

## Failure and data boundaries

- The worker returns immediately to the primary path. Running plus queued shadow
  work is limited to 16 calls across eight threads. Saturation drops shadow work;
  it never waits for Jev. Completed futures are removed.
- Direct calls have a five-second HTTP timeout and no retries. Failures remain
  shadow records. HTTP timeouts apply to network operations, not a whole-call SLA.
- Oversized and credential-shaped prompts are skipped before transmission.
- Each Jev record includes the exact screened decision prompt, primary and shadow
  action/target pairs, primary model, returned Jev model, probabilities, timings,
  and comparison counts. Primary answers are never sent to Jev.
- Records contain private production memory text. Keep them on the production
  data volume. Do not attach raw logs to public issues.
- Logs rotate at 10 MiB with five backups per model (about 60 MiB retained).
  Older records can disappear through rotation; the report describes retained data.
- Queue-drop totals are included in later records and warnings. A restart resets
  the counter; each process has a separate identifier. Drops immediately before a
  shutdown may only be visible in service logs.

## Review after observation

Start with seven days of normal use and aim for at least 200 paired decisions.
These are review targets, not automatic success gates. Extend observation when
coverage lacks duplicates, updates, conflicts, or deletes. Report actual executions,
errors, skipped cases, invalid targets, and action/target agreement separately.

Run the report on the host with access to the private data directory:

```sh
python3 scripts/jev_shadow_report.py --log-dir /data/shadow-logs --days 7
python3 scripts/jev_shadow_report.py --log-dir /data/shadow-logs --days 7 \
  --review-out /private/location/jev-review.jsonl
```

The optional packet samples disagreements and agreements for evidence review.
Agreement does not establish accuracy: both models can make the same mistake.
Review whether Jev avoids unnecessary updates, preserves new information, selects
the right target, and distinguishes unresolved conflicts from corrections. Label
uncertain evidence explicitly. Measure confidence calibration only after independent
reference judgments exist; do not treat model confidence as measured accuracy.

The report does not judge extraction quality, UPDATE replacement prose, final engine
outcomes after guards, retrieval, or downstream answers. Those require separate
product evidence before changing the primary decision path.
