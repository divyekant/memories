# Qdrant Hard Cutover + Scale-Ready Deployment Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Replace FAISS with self-hosted Qdrant, keep existing HTTP/MCP API surface stable, add one-time FAISS->Qdrant migration script, and add configurable N-node Qdrant deployment setup.

**Architecture:** Keep one codebase and one public API contract. Move vector storage and payload persistence to Qdrant, keep the current FastAPI/MCP interfaces unchanged, and enforce entity-level write locks in the app layer for deterministic decision-path writes. Support single-node and cluster modes via deployment configuration (not API branching).

**Tech Stack:** Python 3.11, FastAPI, qdrant-client, ONNX embedder, pytest, Docker Compose, shell scripts.

---

### Task 1: Lock API Contract Before Storage Swap

**Files:**
- Create: `tests/test_api_contract_compat.py`
- Modify: `tests/test_memory_engine.py`
- Modify: `tests/test_metrics_api.py`

**Step 1: Write contract tests for stable response shapes**

```python
def test_health_contract_keys(client):
    data = client.get("/health").json()
    assert data["service"] == "memories"
    assert {"status", "service", "version", "total_memories", "dimension", "model"} <= set(data)


def test_search_contract_shape(client):
    res = client.post("/search", json={"query": "python", "k": 3, "hybrid": True}, headers={"X-API-Key": "test-key"})
    body = res.json()
    assert {"query", "results", "count"} <= set(body)
```

**Step 2: Remove FAISS-internal assertions from tests**

```python
# old (FAISS-specific)
# assert engine.index.ntotal == 1

# new (backend-agnostic)
stats = engine.stats_light()
assert stats["total_memories"] == 1
```

**Step 3: Run focused tests**

Run: `./.venv/bin/python -m pytest -q tests/test_api_contract_compat.py tests/test_memory_engine.py tests/test_metrics_api.py`
Expected: some failures for backend-specific internals until new store is wired.

**Step 4: Commit checkpoint**

```bash
git add tests/test_api_contract_compat.py tests/test_memory_engine.py tests/test_metrics_api.py
git commit -m "test: lock API contract and remove FAISS-specific assertions"
```

### Task 2: Add Qdrant Configuration and Deployment Guards

**Files:**
- Create: `qdrant_config.py`
- Create: `tests/test_qdrant_config.py`
- Modify: `requirements.txt`
- Modify: `docker-compose.yml`
- Modify: `docker-compose.snippet.yml`

**Step 1: Write failing config tests**

```python
def test_requirements_use_qdrant_client_not_faiss():
    req = Path("requirements.txt").read_text()
    assert "qdrant-client" in req
    assert "faiss-cpu" not in req


def test_compose_defines_qdrant_service():
    compose = Path("docker-compose.yml").read_text()
    assert "qdrant:" in compose
    assert "QDRANT_URL" in compose
```

**Step 2: Run tests to confirm RED**

Run: `./.venv/bin/python -m pytest -q tests/test_qdrant_config.py`
Expected: FAIL on missing qdrant config.

**Step 3: Implement env/settings loader**

```python
# qdrant_config.py
@dataclass(frozen=True)
class QdrantSettings:
    url: str
    api_key: str
    collection: str
    wait: bool
    write_ordering: str
    read_consistency: str
    replication_factor: int
    write_consistency_factor: int
```

**Step 4: Update dependencies and compose defaults**

```text
requirements.txt:
- remove faiss-cpu==1.8.0
- add qdrant-client==1.14.2
```

```yaml
# docker-compose.yml (memory service env)
- QDRANT_URL=http://qdrant:6333
- QDRANT_COLLECTION=memories
- QDRANT_WAIT=true
- QDRANT_WRITE_ORDERING=strong
- QDRANT_READ_CONSISTENCY=majority
```

**Step 5: Re-run tests to GREEN**

Run: `./.venv/bin/python -m pytest -q tests/test_qdrant_config.py`
Expected: PASS.

**Step 6: Commit checkpoint**

```bash
git add qdrant_config.py tests/test_qdrant_config.py requirements.txt docker-compose.yml docker-compose.snippet.yml
git commit -m "feat: add qdrant config and compose wiring"
```

### Task 3: Build Qdrant Store Layer (Hard Cutover)

**Files:**
- Create: `qdrant_store.py`
- Create: `tests/test_qdrant_store.py`
- Modify: `memory_engine.py`

**Step 1: Write failing unit tests for store operations**

```python
def test_upsert_and_search_roundtrip(store):
    store.upsert_points([{"id": 1, "vector": [0.1] * 384, "payload": {"text": "hello", "source": "s"}}])
    hits = store.search([0.1] * 384, limit=5)
    assert hits
    assert hits[0]["id"] == 1
```

**Step 2: Run test to confirm RED**

Run: `./.venv/bin/python -m pytest -q tests/test_qdrant_store.py`
Expected: FAIL (`ModuleNotFoundError` / missing store class).

**Step 3: Implement minimal Qdrant store wrapper**

```python
class QdrantStore:
    def ensure_collection(self, dim: int) -> None: ...
    def upsert_points(self, points: list[dict]) -> None: ...
    def search(self, query_vector: list[float], limit: int, score_threshold: float | None = None): ...
    def scroll_all(self): ...
    def delete_points(self, ids: list[int]) -> None: ...
```

**Step 4: Wire MemoryEngine bootstrap to use Qdrant store**

```python
self.store = QdrantStore(settings=QdrantSettings.from_env())
self.store.ensure_collection(self.dim)
```

**Step 5: Re-run store tests**

Run: `./.venv/bin/python -m pytest -q tests/test_qdrant_store.py`
Expected: PASS.

**Step 6: Commit checkpoint**

```bash
git add qdrant_store.py tests/test_qdrant_store.py memory_engine.py
git commit -m "feat: add qdrant storage layer and engine bootstrap"
```

### Task 4: Replace FAISS CRUD/Search in MemoryEngine While Keeping API Semantics

**Files:**
- Modify: `memory_engine.py`
- Modify: `tests/test_memory_engine.py`
- Modify: `tests/test_extract_api.py`

**Step 1: Write failing behavior tests for ID-stable CRUD on Qdrant**

```python
def test_delete_memory_removes_point_without_full_reindex(populated_engine):
    before = populated_engine.stats_light()["total_memories"]
    populated_engine.delete_memory(0)
    after = populated_engine.stats_light()["total_memories"]
    assert after == before - 1
```

**Step 2: Run focused tests (expect RED)**

Run: `./.venv/bin/python -m pytest -q tests/test_memory_engine.py::TestDelete`
Expected: FAIL until delete/search path is migrated.

**Step 3: Implement CRUD/search against Qdrant points + payload**

```python
# add_memories: embed -> qdrant upsert
# search: qdrant.search(..., consistency=settings.read_consistency)
# list_memories: qdrant.scroll with offset/limit
# stats_light: count via qdrant collection info
```

**Step 4: Keep `hybrid_search` contract with backend-agnostic fusion**

```python
# Strategy: vector oversample from Qdrant + lexical rerank over candidate texts only.
# Return field remains rrf_score.
```

**Step 5: Re-run engine + API tests to GREEN**

Run: `./.venv/bin/python -m pytest -q tests/test_memory_engine.py tests/test_extract_api.py`
Expected: PASS.

**Step 6: Commit checkpoint**

```bash
git add memory_engine.py tests/test_memory_engine.py tests/test_extract_api.py
git commit -m "feat: hard-cutover memory engine storage from faiss to qdrant"
```

### Task 5: Add Entity-Level Write Locking for Deterministic Decision Writes

**Files:**
- Create: `entity_locks.py`
- Create: `tests/test_entity_locks.py`
- Modify: `memory_engine.py`

**Step 1: Write failing lock scope tests**

```python
def test_same_entity_serialized(lock_manager):
    # writes to "carto/poet-pads/db" must serialize
    ...


def test_different_entities_parallel(lock_manager):
    # writes to db vs notes should proceed concurrently
    ...
```

**Step 2: Run tests (expect RED)**

Run: `./.venv/bin/python -m pytest -q tests/test_entity_locks.py`
Expected: FAIL until lock manager exists.

**Step 3: Implement keyed lock manager**

```python
with lock_manager.acquire_many(["tenant:carto/poet-pads/db"]):
    # single-entity critical section
    ...
```

**Step 4: Integrate lock strategy into writes**

```python
# /memory/add -> key from source
# /memory/add-batch -> sorted unique keys
# delete-by-source/prefix ops -> use broader prefix/global lock
```

**Step 5: Re-run tests to GREEN**

Run: `./.venv/bin/python -m pytest -q tests/test_entity_locks.py tests/test_memory_engine.py::TestAddAndSearch`
Expected: PASS.

**Step 6: Commit checkpoint**

```bash
git add entity_locks.py tests/test_entity_locks.py memory_engine.py
git commit -m "feat: add entity-level write locks for deterministic updates"
```

### Task 6: Implement One-Time FAISS -> Qdrant Migration Script

**Files:**
- Create: `scripts/migrate_faiss_to_qdrant.py`
- Create: `tests/test_migrate_faiss_to_qdrant.py`
- Modify: `README.md`

**Step 1: Write failing migration tests**

```python
def test_migration_reads_faiss_and_metadata_and_writes_qdrant(tmp_path):
    # setup fake index.faiss + metadata.json
    # assert output manifest contains migrated_count
    ...
```

**Step 2: Run tests (expect RED)**

Run: `./.venv/bin/python -m pytest -q tests/test_migrate_faiss_to_qdrant.py`
Expected: FAIL until script exists.

**Step 3: Implement migration script with idempotency + dry-run**

```bash
./.venv/bin/python scripts/migrate_faiss_to_qdrant.py \
  --data-dir ./data \
  --qdrant-url http://localhost:6333 \
  --collection memories \
  --batch-size 256 \
  --dry-run
```

```python
# flow:
# 1) load index.faiss + metadata.json + config.json
# 2) reconstruct vectors from FAISS (no re-embedding)
# 3) create/validate qdrant collection
# 4) upsert points in batches
# 5) write migration manifest: data/migrations/faiss_to_qdrant_v1.json
```

**Step 4: Re-run migration tests**

Run: `./.venv/bin/python -m pytest -q tests/test_migrate_faiss_to_qdrant.py`
Expected: PASS.

**Step 5: Commit checkpoint**

```bash
git add scripts/migrate_faiss_to_qdrant.py tests/test_migrate_faiss_to_qdrant.py README.md
git commit -m "feat: add one-time faiss to qdrant migration script"
```

### Task 7: Rework Backup/Restore to Be Qdrant-Backed (API Unchanged)

**Files:**
- Modify: `memory_engine.py`
- Modify: `scripts/backup.sh`
- Modify: `cloud_sync.py`
- Modify: `tests/test_cloud_sync.py`
- Modify: `tests/test_memory_engine.py`

**Step 1: Write failing backup tests for new artifact format**

```python
def test_backup_contains_collection_export(populated_engine):
    b = populated_engine.create_backup(prefix="test")
    assert (b / "points.jsonl").exists()
    assert (b / "collection.json").exists()
```

**Step 2: Run tests (expect RED)**

Run: `./.venv/bin/python -m pytest -q tests/test_memory_engine.py::TestBackupRestore`
Expected: FAIL on old `index.faiss` assertions.

**Step 3: Implement logical export/import backup format**

```text
backup dir contents:
- points.jsonl        # id + vector + payload
- collection.json     # dim, distance, collection settings
- config.json         # engine metadata
```

**Step 4: Update backup shell script to copy new backup artifacts**

```bash
# stop copying index.faiss directly
# rely on /backup response path and snapshot directory contents
```

**Step 5: Re-run backup tests**

Run: `./.venv/bin/python -m pytest -q tests/test_memory_engine.py::TestBackupRestore tests/test_cloud_sync.py`
Expected: PASS.

**Step 6: Commit checkpoint**

```bash
git add memory_engine.py scripts/backup.sh cloud_sync.py tests/test_cloud_sync.py tests/test_memory_engine.py
git commit -m "feat: switch backup and restore artifacts to qdrant export format"
```

### Task 8: Add N-Node Qdrant Setup Option

**Files:**
- Create: `scripts/render_qdrant_cluster_compose.py`
- Create: `docker-compose.qdrant-cluster.template.yml`
- Create: `tests/test_qdrant_cluster_compose.py`
- Modify: `docker-compose.yml`
- Modify: `GETTING_STARTED.md`

**Step 1: Write failing generator tests**

```python
def test_render_three_node_cluster(tmp_path):
    out = tmp_path / "cluster.yml"
    render(nodes=3, output=out)
    text = out.read_text()
    assert "qdrant_node1:" in text
    assert "qdrant_node3:" in text
    assert "--bootstrap" in text
```

**Step 2: Run tests (expect RED)**

Run: `./.venv/bin/python -m pytest -q tests/test_qdrant_cluster_compose.py`
Expected: FAIL until renderer exists.

**Step 3: Implement renderer for arbitrary N>=1**

```bash
./.venv/bin/python scripts/render_qdrant_cluster_compose.py --nodes 3 --output docker-compose.qdrant-cluster.generated.yml
```

```text
node1 command: ./qdrant --uri http://qdrant_node1:6335
nodeN command: ./qdrant --bootstrap http://qdrant_node1:6335 --uri http://qdrant_nodeN:6335
```

**Step 4: Add app-side env for cluster durability knobs**

```text
QDRANT_REPLICATION_FACTOR=3
QDRANT_WRITE_CONSISTENCY_FACTOR=2
QDRANT_READ_CONSISTENCY=majority
QDRANT_WRITE_ORDERING=strong
```

**Step 5: Re-run tests to GREEN**

Run: `./.venv/bin/python -m pytest -q tests/test_qdrant_cluster_compose.py tests/test_qdrant_config.py`
Expected: PASS.

**Step 6: Commit checkpoint**

```bash
git add scripts/render_qdrant_cluster_compose.py docker-compose.qdrant-cluster.template.yml tests/test_qdrant_cluster_compose.py docker-compose.yml GETTING_STARTED.md
git commit -m "feat: add configurable n-node qdrant cluster setup"
```

### Task 9: Update Documentation and Operational Runbooks

**Files:**
- Modify: `README.md`
- Modify: `PROJECT.md`
- Modify: `docs/architecture.md`
- Modify: `docs/decisions.md`
- Create: `docs/plans/2026-02-18-qdrant-cutover-rollout-runbook.md`

**Step 1: Document hard cutover behavior and migration flow**

```markdown
## Migration (one-time)
1. Stop writes.
2. Run `scripts/migrate_faiss_to_qdrant.py`.
3. Validate counts and search smoke checks.
4. Start service with Qdrant backend.
```

**Step 2: Document single-node and N-node deployments**

```bash
# single-node
docker compose up -d --build

# 3-node cluster
python scripts/render_qdrant_cluster_compose.py --nodes 3 --output docker-compose.qdrant-cluster.generated.yml
docker compose -f docker-compose.yml -f docker-compose.qdrant-cluster.generated.yml up -d
```

**Step 3: Add rollback procedure**

```markdown
Rollback: stop app, restore from latest pre-cutover backup, restart previous image tag.
```

**Step 4: Commit checkpoint**

```bash
git add README.md PROJECT.md docs/architecture.md docs/decisions.md docs/plans/2026-02-18-qdrant-cutover-rollout-runbook.md
git commit -m "docs: add qdrant cutover deployment and rollback runbooks"
```

### Task 10: Full Verification and PR Readiness

**Files:**
- Modify: `docs/benchmarks/2026-02-18-qdrant-cutover-validation.md`

**Step 1: Run full test suite**

Run: `./.venv/bin/python -m pytest -q`
Expected: all tests pass.

**Step 2: Run single-node smoke tests**

```bash
docker compose up -d --build
curl -s http://localhost:8900/health
curl -s -X POST http://localhost:8900/memory/add -H 'Content-Type: application/json' -d '{"text":"smoke","source":"ops/smoke"}'
curl -s -X POST http://localhost:8900/search -H 'Content-Type: application/json' -d '{"query":"smoke","k":5,"hybrid":true}'
```

Expected: `service=memories`, add succeeds, search returns >=1 result.

**Step 3: Run cluster-mode smoke tests (N=3)**

```bash
python scripts/render_qdrant_cluster_compose.py --nodes 3 --output docker-compose.qdrant-cluster.generated.yml
docker compose -f docker-compose.yml -f docker-compose.qdrant-cluster.generated.yml up -d
# repeat add/search smoke checks
```

Expected: successful writes with strong ordering and consistent reads.

**Step 4: Validate migration script on copy of production data**

```bash
python scripts/migrate_faiss_to_qdrant.py --data-dir ./data --qdrant-url http://localhost:6333 --collection memories --verify
```

Expected: migrated count equals FAISS metadata count; verification exits 0.

**Step 5: Capture validation evidence**

```markdown
Record: test summary, smoke outputs, migration verification, and known limitations.
```

**Step 6: Commit checkpoint**

```bash
git add docs/benchmarks/2026-02-18-qdrant-cutover-validation.md
git commit -m "chore: record qdrant cutover validation evidence"
```

---

## Implementation Notes

- Keep API paths and JSON shapes unchanged for: `/search`, `/memory/*`, `/memories`, `/stats`, `/health`, `/backup`, `/restore`, MCP tools.
- Consistency defaults for decision-path reads/writes:
  - writes: `wait=true`, `ordering=strong`
  - reads: `consistency=majority`
- Lock scope:
  - entity write key = `<tenant>:<source>` (or `default:<source>` when tenant not provided)
  - bulk prefix operations require broader lock.
- Hard cutover policy: no FAISS fallback in runtime after migration is complete.

## Dependencies and Doc References

- Qdrant distributed consistency and ordering: https://qdrant.tech/documentation/guides/distributed_deployment/
- Qdrant upsert parameters (`wait`, `ordering`): https://api.qdrant.tech/api-reference/points/upsert-points
- Qdrant query consistency: https://api.qdrant.tech/master/api-reference/search/query-points
- Qdrant create collection replication/write consistency: https://api.qdrant.tech/v-1-14-x/api-reference/collections/create-collection
- Qdrant snapshots/backup concepts: https://qdrant.tech/documentation/concepts/snapshots/
- Qdrant distributed docker demo: https://raw.githubusercontent.com/qdrant/demo-distributed-deployment-docker/master/docker-compose.yaml

