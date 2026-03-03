# Cloud Sync Feature

**Status:** ✅ Implemented (Phase 1)  
**Provider Support:** S3-compatible (AWS S3, Backblaze B2, MinIO, DigitalOcean Spaces, Wasabi, etc.)

---

## Installation

Cloud sync is **opt-in** to keep the Docker image lean (saves ~80MB).

### Option 1: Docker Build with Cloud Sync

Build the image with cloud sync enabled:

```bash
docker build --target core --build-arg ENABLE_CLOUD_SYNC=true -t memories:core .
# or: docker build --target extract --build-arg ENABLE_CLOUD_SYNC=true -t memories:extract .
```

### Option 2: Rebuild with Cloud Sync (if already running)

With the uv-based Docker image, runtime package installs are not supported. Rebuild the image with cloud sync:

```bash
docker compose build --build-arg ENABLE_CLOUD_SYNC=true memories
docker compose up -d memories
```

### Option 3: Local Development

```bash
uv sync --extra cloud
```

---

## Overview

Cloud Sync automatically backs up your Memories memory index to S3-compatible cloud storage. This enables:

- **Cross-machine sync** - Use the same memories on multiple machines
- **Disaster recovery** - Never lose your memories if a machine fails
- **Easy migration** - Set up on a new machine and automatically pull existing memories

---

## Quick Start

### 1. Enable Cloud Sync

Add these environment variables to your `docker-compose.yml`:

```yaml
environment:
  - CLOUD_SYNC_ENABLED=true
  - CLOUD_SYNC_BUCKET=my-memories
  - CLOUD_SYNC_REGION=us-east-1
  - CLOUD_SYNC_ACCESS_KEY=AKIA...
  - CLOUD_SYNC_SECRET_KEY=...
```

### 2. Restart the container

```bash
docker compose down
docker compose up -d
```

### 3. Verify it's working

```bash
curl http://localhost:8900/sync/status
```

---

## Configuration

### Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `CLOUD_SYNC_ENABLED` | No | `false` | Enable cloud sync |
| `CLOUD_SYNC_BUCKET` | Yes* | - | S3 bucket name |
| `CLOUD_SYNC_REGION` | No | `us-east-1` | AWS region |
| `CLOUD_SYNC_PREFIX` | No | `memories/` | Path prefix in bucket |
| `CLOUD_SYNC_ACCESS_KEY` | No** | - | S3 access key |
| `CLOUD_SYNC_SECRET_KEY` | No** | - | S3 secret key |
| `CLOUD_SYNC_ENDPOINT` | No | - | Custom endpoint (for MinIO, B2, etc.) |

\* Required if `CLOUD_SYNC_ENABLED=true`  
\*\* Optional if using IAM roles or instance profiles

---

## How It Works

### Automatic Upload

Every time the memory engine creates a backup (e.g., after adding memories), it automatically uploads that backup to your S3 bucket.

**Upload triggers:**
- After every write operation that creates a backup
- Manual trigger via `/sync/upload` API

### Automatic Download

On container startup, if the local index is empty, the service automatically downloads the latest backup from cloud storage and restores it.

This means:
- **First machine:** Memories are uploaded to cloud automatically
- **Second machine:** Memories are auto-downloaded on first start

---

## API Endpoints

### GET /sync/status

Check cloud sync status and compare local vs remote backups.

```bash
curl http://localhost:8900/sync/status \
  -H "X-API-Key: your-key"
```

**Response:**
```json
{
  "enabled": true,
  "latest_remote": "manual_20260214_120000",
  "latest_local": "auto_20260214_120500",
  "remote_count": 5,
  "local_count": 10
}
```

---

### POST /sync/upload

Manually trigger a backup and upload to cloud.

```bash
curl -X POST http://localhost:8900/sync/upload \
  -H "X-API-Key: your-key"
```

---

### GET /sync/snapshots

List all backups available in cloud storage.

```bash
curl http://localhost:8900/sync/snapshots \
  -H "X-API-Key: your-key"
```

**Response:**
```json
{
  "snapshots": [
    {
      "name": "manual_20260214_120000",
      "s3_prefix": "memories/manual_20260214_120000/"
    },
    {
      "name": "auto_20260213_020000",
      "s3_prefix": "memories/auto_20260213_020000/"
    }
  ],
  "count": 2
}
```

---

### POST /sync/download

Download a backup from cloud (does not automatically restore).

```bash
curl -X POST "http://localhost:8900/sync/download?confirm=true" \
  -H "X-API-Key: your-key"
```

**Query params:**
- `backup_name` (optional) - Specific backup to download (defaults to latest)
- `confirm` (required) - Must be `true` to proceed

---

### POST /sync/restore/{backup_name}

Download and restore a backup from cloud in one step.

```bash
curl -X POST "http://localhost:8900/sync/restore/manual_20260214_120000?confirm=true" \
  -H "X-API-Key: your-key"
```

⚠️ **Warning:** This will overwrite your local index!

---

## Provider-Specific Setup

### AWS S3

```yaml
environment:
  - CLOUD_SYNC_ENABLED=true
  - CLOUD_SYNC_BUCKET=my-bucket
  - CLOUD_SYNC_REGION=us-east-1
  - CLOUD_SYNC_ACCESS_KEY=AKIA...
  - CLOUD_SYNC_SECRET_KEY=...
```

**Create bucket:**
```bash
aws s3 mb s3://my-memories --region us-east-1
```

---

### Backblaze B2

```yaml
environment:
  - CLOUD_SYNC_ENABLED=true
  - CLOUD_SYNC_BUCKET=my-bucket
  - CLOUD_SYNC_REGION=us-west-000
  - CLOUD_SYNC_ENDPOINT=https://s3.us-west-000.backblazeb2.com
  - CLOUD_SYNC_ACCESS_KEY=...  # Application Key ID
  - CLOUD_SYNC_SECRET_KEY=...  # Application Key
```

---

### MinIO (Self-Hosted)

```yaml
environment:
  - CLOUD_SYNC_ENABLED=true
  - CLOUD_SYNC_BUCKET=memories
  - CLOUD_SYNC_REGION=us-east-1
  - CLOUD_SYNC_ENDPOINT=http://minio.local:9000
  - CLOUD_SYNC_ACCESS_KEY=minioadmin
  - CLOUD_SYNC_SECRET_KEY=minioadmin
```

---

### DigitalOcean Spaces

```yaml
environment:
  - CLOUD_SYNC_ENABLED=true
  - CLOUD_SYNC_BUCKET=my-space
  - CLOUD_SYNC_REGION=nyc3
  - CLOUD_SYNC_ENDPOINT=https://nyc3.digitaloceanspaces.com
  - CLOUD_SYNC_ACCESS_KEY=...
  - CLOUD_SYNC_SECRET_KEY=...
```

---

## Security Best Practices

1. **Use IAM roles** when running on AWS EC2 (no need for access keys)
2. **Create a dedicated S3 user** with minimal permissions:
   ```json
   {
     "Version": "2012-10-17",
     "Statement": [
       {
         "Effect": "Allow",
         "Action": [
           "s3:PutObject",
           "s3:GetObject",
           "s3:ListBucket"
         ],
         "Resource": [
           "arn:aws:s3:::my-memories",
           "arn:aws:s3:::my-memories/*"
         ]
       }
     ]
   }
   ```

3. **Use Docker secrets** instead of plain env vars:
   ```yaml
   secrets:
     cloud_sync_secret_key:
       file: ./secrets/cloud_sync_secret_key.txt
   environment:
     - CLOUD_SYNC_SECRET_KEY_FILE=/run/secrets/cloud_sync_secret_key
   ```

4. **Enable bucket encryption** (S3 server-side encryption)

---

## Troubleshooting

### Cloud sync not enabled

**Symptom:** `/sync/status` returns `{"enabled": false}`

**Solutions:**
1. Check `CLOUD_SYNC_ENABLED=true` is set
2. Verify `CLOUD_SYNC_BUCKET` is configured
3. Check container logs: `docker compose logs memories`

---

### Upload fails

**Symptom:** Backups create locally but don't appear in S3

**Solutions:**
1. Check credentials are correct
2. Verify bucket exists: `aws s3 ls s3://my-bucket`
3. Test connectivity: `docker compose exec memories ping s3.amazonaws.com`
4. Check IAM permissions

---

### Auto-download doesn't work

**Symptom:** New machine doesn't restore from cloud on startup

**Solutions:**
1. Ensure local `/data` directory is empty
2. Check logs during startup
3. Verify snapshots exist: `curl http://localhost:8900/sync/snapshots`

---

## Roadmap

### Phase 2 (Future)
- [ ] Encryption at rest (AES-256 before upload)
- [ ] Differential sync (only upload changed files)
- [ ] Scheduled uploads (cron-style)
- [ ] Google Cloud Storage support
- [ ] Azure Blob Storage support

### Phase 3 (Future)
- [ ] Self-hosted sync (rsync/SFTP)
- [ ] Multi-version retention policies
- [ ] Conflict resolution strategies

---

## License

Same as main project.
