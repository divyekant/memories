# Decisions

- Use Qdrant for vector storage, because it supports sparse vectors.
- Keep the extract queue on disk, so that a restart does not drop jobs.

## Open

The rollback window is 7 days.
