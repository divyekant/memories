# Cloud Sync Feature - Design Decisions

**Date:** 2026-02-14  
**Contributors:** Darshan + Dan

---

## Decisions

### Phase 1 (Initial PR)

✅ **Storage Provider:** S3-compatible only (AWS S3, Backblaze B2, MinIO, DigitalOcean Spaces, etc.)  
✅ **Sync Strategy:** Upload timestamped backup snapshots to cloud  
✅ **Upload Triggers:** Scheduled (cron) + Manual API  
✅ **Download Triggers:** Auto on startup (if local empty) + Manual API  
✅ **Conflict Resolution:** Never auto-merge - fail and warn user  
✅ **Configuration:** Environment variables (Docker-friendly)  
✅ **Feature Type:** Opt-in via `CLOUD_SYNC_ENABLED=true`  

❌ **Encryption:** Phase 2 (not in initial PR)  
❌ **Multi-provider:** Phase 2 (just S3-compatible for now)  

---

## Environment Variables

```yaml
CLOUD_SYNC_ENABLED=true              # Enable cloud sync feature
CLOUD_SYNC_PROVIDER=s3               # Always "s3" for Phase 1
CLOUD_SYNC_BUCKET=my-memories    # S3 bucket name
CLOUD_SYNC_ENDPOINT=                 # Optional: custom endpoint (for MinIO, B2, etc.)
CLOUD_SYNC_REGION=us-east-1          # AWS region
CLOUD_SYNC_ACCESS_KEY=AKIA...        # S3 access key
CLOUD_SYNC_SECRET_KEY=...            # S3 secret key
CLOUD_SYNC_SCHEDULE=0 2 * * *        # Cron format (default: 2am daily)
CLOUD_SYNC_PREFIX=memories/      # Optional: prefix path in bucket
```

---

## New API Endpoints

```
POST /sync/upload           # Manual upload now (returns snapshot name)
POST /sync/download         # Manual download (requires confirmation param)
GET  /sync/status           # Last sync info, next scheduled, remote vs local
GET  /sync/snapshots        # List remote snapshots
POST /sync/restore/{name}   # Restore from specific remote snapshot
```

---

## Implementation Plan

1. **New file:** `cloud_sync.py`
   - S3Client wrapper class
   - Upload/download logic
   - Snapshot listing

2. **Modify:** `memory_engine.py`
   - Hook: After backup creation → trigger upload
   - Hook: On init (if empty) → check cloud + download

3. **Modify:** `app.py`
   - Add `/sync/*` endpoints
   - Initialize CloudSync on startup (if enabled)
   - Schedule uploads via background task

4. **Modify:** `requirements.txt`
   - Add `boto3`
   - Add `schedule` (or use existing async scheduler)

5. **Update:** `docker-compose.yml` (example with env vars)

6. **Update:** `README.md` (cloud sync section)

---

## Testing Checklist

- [ ] Upload backup to S3
- [ ] Download backup from S3
- [ ] Auto-download on empty startup
- [ ] List remote snapshots
- [ ] Restore from specific snapshot
- [ ] Conflict detection (local != remote)
- [ ] Works with MinIO (S3-compatible)
- [ ] Scheduled uploads work
- [ ] Feature disabled when CLOUD_SYNC_ENABLED=false

---

## Future Phases

### Phase 2
- Encryption at rest (AES-256 before upload)
- Differential sync (only upload changed files)
- More providers (GCS, Azure)

### Phase 3
- Self-hosted sync (rsync/SFTP)
- Multi-version retention policies
- Automatic conflict resolution strategies
