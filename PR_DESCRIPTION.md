# Add S3-Compatible Cloud Sync for Automatic Backup Management

## 🎯 Overview

This PR adds cloud sync functionality to FAISS Memory, enabling automatic backup uploads to S3-compatible storage and seamless cross-machine synchronization.

## ✨ Features

### Core Functionality
- **Automatic Upload**: Backups are automatically uploaded to S3 after creation
- **Automatic Download**: On startup, if local index is empty, automatically downloads and restores the latest backup from cloud
- **Multi-Provider Support**: Works with any S3-compatible storage:
  - AWS S3
  - Backblaze B2
  - MinIO (self-hosted)
  - DigitalOcean Spaces
  - Wasabi
  - Any S3-compatible service

### New API Endpoints
- `GET /sync/status` - Check sync status and compare local vs remote backups
- `POST /sync/upload` - Manually trigger backup upload
- `POST /sync/download` - Download a backup from cloud
- `GET /sync/snapshots` - List all remote backups
- `POST /sync/restore/{backup_name}` - Download and restore in one step

### Configuration
- **Opt-in via environment variables** - Feature is disabled by default
- Simple configuration via `docker-compose.yml`:
  ```yaml
  environment:
    - CLOUD_SYNC_ENABLED=true
    - CLOUD_SYNC_BUCKET=my-bucket
    - CLOUD_SYNC_REGION=us-east-1
    - CLOUD_SYNC_ACCESS_KEY=AKIA...
    - CLOUD_SYNC_SECRET_KEY=...
  ```

## 📁 Changes

### New Files
- `cloud_sync.py` - S3 client wrapper with upload/download logic
- `CLOUD_SYNC_DESIGN.md` - Design decisions and architecture documentation
- `CLOUD_SYNC_README.md` - Complete user documentation with examples

### Modified Files
- `memory_engine.py` - Integrated cloud sync hooks (auto-upload after backup, auto-download on startup)
- `app.py` - Added 5 new `/sync/*` endpoints
- `requirements.txt` - Added `boto3==1.35.36` dependency
- `docker-compose.yml` - Added example environment variable configuration (commented out, opt-in)

## 🧪 Use Cases

### 1. Multi-Machine Sync
Run FAISS Memory on laptop + server with same memory index:
```bash
# Machine 1: memories automatically sync to S3
# Machine 2: On first start, automatically downloads from S3
```

### 2. Disaster Recovery
Never lose your memories if a machine fails - backups are safely stored in cloud.

### 3. Easy Migration
Set up on a new machine and automatically pull existing memories:
```bash
docker compose up -d
# Automatically downloads latest backup and restores
```

## 🔒 Security

- **Opt-in by default** - Feature disabled unless explicitly enabled
- **Supports IAM roles** - No need for hardcoded credentials on AWS EC2
- **Custom endpoints** - Can use self-hosted MinIO for complete data control
- **No auto-merge** - Never automatically overwrites data without confirmation

## 📝 Documentation

Complete documentation provided in `CLOUD_SYNC_README.md` including:
- Quick start guide
- Configuration reference
- API endpoint examples
- Provider-specific setup (AWS, Backblaze, MinIO, etc.)
- Security best practices
- Troubleshooting guide

## 🔮 Future Enhancements (Phase 2)

- Encryption at rest (AES-256 before upload)
- Differential sync (only upload changed files)
- Scheduled uploads (cron-style)
- Google Cloud Storage support
- Azure Blob Storage support

## ⚙️ Testing

Tested with:
- ✅ AWS S3
- ✅ Local MinIO instance
- ✅ Auto-download on empty startup
- ✅ Manual upload/download via API
- ✅ Snapshot listing
- ✅ Restore from specific backup

## 📦 Breaking Changes

None - this is a purely additive feature with opt-in activation.

## 🙏 Acknowledgments

Feature requested and designed in collaboration with @darshanpania to enable portable memory across multiple machines.

---

**Ready for review!** This is Phase 1 - a solid foundation for cloud sync with room to expand in future phases.
