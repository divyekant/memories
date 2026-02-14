#!/bin/bash
#
# FAISS Memory - Off-site Backup to Google Drive (Optional)
# Uploads the latest snapshot hourly and maintains 7-day retention
#
# Usage:
#   ./scripts/backup-gdrive.sh              # Upload latest (hourly throttled)
#   ./scripts/backup-gdrive.sh --force      # Upload now (skip throttle)
#   ./scripts/backup-gdrive.sh --test       # Dry run
#   ./scripts/backup-gdrive.sh --setup      # Create Drive folder + test auth
#   ./scripts/backup-gdrive.sh --cleanup    # Only run Drive cleanup
#
# Prerequisites:
#   1. Install gog CLI: https://github.com/skratchdot/gog
#   2. Authenticate:    gog auth add your-email@gmail.com --services drive
#   3. Set env var:     export GDRIVE_ACCOUNT="your-email@gmail.com"
#
# Environment:
#   GDRIVE_ACCOUNT       - Google account email (required)
#   GDRIVE_FOLDER_NAME   - Drive folder name (default: faiss-memory-backups)
#   BACKUP_DIR           - Local backup dir (default: ~/backups/faiss-memory)
#   UPLOAD_INTERVAL_MIN  - Min minutes between uploads (default: 55)
#   GDRIVE_RETENTION_DAYS - Days to keep on Drive (default: 7)
#

set -e

# Ensure tools are in PATH (cron has minimal PATH)
export PATH="/usr/local/bin:/opt/homebrew/bin:$PATH"

# Source env vars (cron doesn't load shell profile)
[ -f "$HOME/.zshrc" ] && source "$HOME/.zshrc" 2>/dev/null || true

# Configuration (all overridable via env vars)
BACKUP_DIR="${BACKUP_DIR:-$HOME/backups/faiss-memory}"
LOG_FILE="$BACKUP_DIR/gdrive.log"
GDRIVE_ACCOUNT="${GDRIVE_ACCOUNT}"
GDRIVE_FOLDER_NAME="${GDRIVE_FOLDER_NAME:-faiss-memory-backups}"
FOLDER_ID_FILE="$BACKUP_DIR/.gdrive_folder_id"
LAST_UPLOAD_FILE="$BACKUP_DIR/.gdrive_last_upload"
UPLOAD_INTERVAL_MIN="${UPLOAD_INTERVAL_MIN:-55}"
RETENTION_DAYS="${GDRIVE_RETENTION_DAYS:-7}"

# Validate required config
if [ -z "$GDRIVE_ACCOUNT" ]; then
    echo "[ERROR] GDRIVE_ACCOUNT not set. Export it in your shell profile:"
    echo "  export GDRIVE_ACCOUNT=\"your-email@gmail.com\""
    exit 1
fi

# Parse arguments
TEST_MODE=false
SETUP_MODE=false
FORCE_MODE=false
CLEANUP_ONLY=false
for arg in "$@"; do
    case $arg in
        --test) TEST_MODE=true ;;
        --setup) SETUP_MODE=true ;;
        --force) FORCE_MODE=true ;;
        --cleanup) CLEANUP_ONLY=true ;;
    esac
done

# Logging
log() {
    local level="$1"
    local message="$2"
    local timestamp=$(date +"%Y-%m-%d %H:%M:%S")
    mkdir -p "$BACKUP_DIR"
    echo "[$timestamp] [$level] $message" >> "$LOG_FILE"
    echo "[$level] $message"
}

# Check if enough time has passed since last upload
should_upload() {
    if [ "$FORCE_MODE" = true ]; then
        return 0
    fi

    if [ ! -f "$LAST_UPLOAD_FILE" ]; then
        return 0
    fi

    local last_upload=$(cat "$LAST_UPLOAD_FILE" 2>/dev/null || echo "0")
    local now=$(date +%s)
    local elapsed=$(( (now - last_upload) / 60 ))

    if [ "$elapsed" -ge "$UPLOAD_INTERVAL_MIN" ]; then
        return 0
    fi

    log "INFO" "Skipping upload (last upload ${elapsed}min ago, interval=${UPLOAD_INTERVAL_MIN}min)"
    return 1
}

# Record upload timestamp
record_upload() {
    date +%s > "$LAST_UPLOAD_FILE"
}

# Get or create the Google Drive folder
get_folder_id() {
    # Check cached folder ID
    if [ -f "$FOLDER_ID_FILE" ]; then
        local cached_id=$(cat "$FOLDER_ID_FILE")
        if [ -n "$cached_id" ]; then
            echo "$cached_id"
            return 0
        fi
    fi

    # Search for existing folder
    log "INFO" "Searching for '$GDRIVE_FOLDER_NAME' folder on Drive..."
    local folder_id=$(gog drive ls \
        --account="$GDRIVE_ACCOUNT" \
        --json --results-only \
        --query "name = '$GDRIVE_FOLDER_NAME' and mimeType = 'application/vnd.google-apps.folder' and trashed = false" \
        2>/dev/null | python3 -c "import sys,json; files=json.load(sys.stdin); print(files[0]['id'] if files else '')" 2>/dev/null || echo "")

    if [ -n "$folder_id" ]; then
        log "INFO" "Found existing folder: $folder_id"
        echo "$folder_id" > "$FOLDER_ID_FILE"
        echo "$folder_id"
        return 0
    fi

    # Create folder
    log "INFO" "Creating '$GDRIVE_FOLDER_NAME' folder on Drive..."
    folder_id=$(gog drive mkdir "$GDRIVE_FOLDER_NAME" \
        --account="$GDRIVE_ACCOUNT" \
        --json --results-only \
        2>/dev/null | python3 -c "import sys,json; print(json.load(sys.stdin)['id'])" 2>/dev/null || echo "")

    if [ -n "$folder_id" ]; then
        log "INFO" "Created folder: $folder_id"
        echo "$folder_id" > "$FOLDER_ID_FILE"
        echo "$folder_id"
        return 0
    fi

    log "ERROR" "Failed to get or create Drive folder"
    return 1
}

# Upload latest snapshot as a tar.gz
upload_latest() {
    # Throttle: only upload once per hour
    if ! should_upload; then
        return 0
    fi

    # Find the most recent snapshot directory
    local latest=$(ls -dt "$BACKUP_DIR"/20* 2>/dev/null | head -1)

    if [ -z "$latest" ] || [ ! -d "$latest" ]; then
        log "ERROR" "No snapshot directories found in $BACKUP_DIR"
        return 1
    fi

    local snapshot_name=$(basename "$latest")
    local tar_file="$BACKUP_DIR/${snapshot_name}.tar.gz"

    log "INFO" "Latest snapshot: $snapshot_name"

    if [ "$TEST_MODE" = true ]; then
        log "INFO" "[DRY RUN] Would upload: $snapshot_name"
        return 0
    fi

    # Create tar.gz of the snapshot
    tar -czf "$tar_file" -C "$BACKUP_DIR" "$snapshot_name" 2>/dev/null
    local size=$(du -h "$tar_file" | cut -f1)

    # Get folder ID
    local folder_id=$(get_folder_id)
    if [ -z "$folder_id" ]; then
        log "ERROR" "Cannot determine Drive folder ID"
        rm -f "$tar_file"
        return 1
    fi

    # Upload
    log "INFO" "Uploading ${snapshot_name}.tar.gz ($size) to Google Drive..."
    local start_time=$(date +%s)

    if gog drive upload "$tar_file" \
        --account="$GDRIVE_ACCOUNT" \
        --parent="$folder_id" \
        --no-input \
        2>>"$LOG_FILE"; then

        local end_time=$(date +%s)
        local duration=$((end_time - start_time))
        log "SUCCESS" "Uploaded ${snapshot_name}.tar.gz ($size) in ${duration}s"
        record_upload

        # Remove local tar.gz (keep the directory snapshot)
        rm -f "$tar_file"

        # Cleanup old backups on Drive
        cleanup_old_drive_backups "$folder_id"
        return 0
    else
        log "ERROR" "Failed to upload ${snapshot_name}.tar.gz"
        rm -f "$tar_file"
        return 1
    fi
}

# Remove backups older than RETENTION_DAYS from Drive
cleanup_old_drive_backups() {
    local folder_id="$1"

    log "INFO" "Checking Drive backups (retention: ${RETENTION_DAYS} days)..."

    # Calculate cutoff date
    local cutoff_date=$(date -v-${RETENTION_DAYS}d +"%Y-%m-%d" 2>/dev/null || date -d "-${RETENTION_DAYS} days" +"%Y-%m-%d" 2>/dev/null)

    if [ -z "$cutoff_date" ]; then
        log "ERROR" "Cannot calculate cutoff date"
        return 1
    fi

    # List all files in the backup folder
    local files_json=$(gog drive ls \
        --account="$GDRIVE_ACCOUNT" \
        --parent="$folder_id" \
        --json --results-only \
        2>/dev/null || echo "[]")

    # Find files older than cutoff (filename starts with date: YYYY-MM-DD-*)
    local to_delete=$(echo "$files_json" | python3 -c "
import sys, json
cutoff = '$cutoff_date'
files = json.load(sys.stdin)
for f in files:
    name = f.get('name', '')
    file_date = name[:10] if len(name) >= 10 else ''
    if file_date and file_date < cutoff:
        print(f['id'] + '\t' + name)
" 2>/dev/null || echo "")

    if [ -z "$to_delete" ]; then
        log "INFO" "No old backups to clean up on Drive"
        return 0
    fi

    local deleted=0
    while IFS=$'\t' read -r file_id file_name; do
        if [ -n "$file_id" ]; then
            if [ "$TEST_MODE" = true ]; then
                log "INFO" "[DRY RUN] Would delete: $file_name"
                deleted=$((deleted + 1))
            else
                if gog drive delete "$file_id" --account="$GDRIVE_ACCOUNT" --force --no-input 2>/dev/null; then
                    deleted=$((deleted + 1))
                    log "INFO" "Deleted old Drive backup: $file_name"
                else
                    log "ERROR" "Failed to delete: $file_name ($file_id)"
                fi
            fi
        fi
    done <<< "$to_delete"

    if [ "$deleted" -gt 0 ]; then
        log "SUCCESS" "Cleaned up $deleted old backup(s) from Drive"
    fi
}

# Setup: create folder and test auth
setup() {
    log "INFO" "=== Setting up Google Drive backup for FAISS Memory ==="

    # Test auth
    if ! gog drive ls --account="$GDRIVE_ACCOUNT" --json --results-only 2>/dev/null | head -1 >/dev/null; then
        log "ERROR" "Drive auth not configured. Run: gog auth add $GDRIVE_ACCOUNT --services drive"
        return 1
    fi
    log "SUCCESS" "Drive auth OK for $GDRIVE_ACCOUNT"

    local folder_id=$(get_folder_id)
    if [ -n "$folder_id" ]; then
        log "SUCCESS" "Setup complete. Folder: $GDRIVE_FOLDER_NAME ($folder_id)"

        # Show current Drive usage
        local files_json=$(gog drive ls --account="$GDRIVE_ACCOUNT" --parent="$folder_id" --json --results-only 2>/dev/null || echo "[]")
        local stats=$(echo "$files_json" | python3 -c "
import sys, json
files = json.load(sys.stdin)
total_size = sum(int(f.get('size', 0)) for f in files)
print(f'{len(files)} files, {total_size / 1024:.1f} KB')
" 2>/dev/null || echo "unknown")
        log "INFO" "Current Drive usage: $stats"
        log "INFO" "Retention: ${RETENTION_DAYS} days, Upload interval: ${UPLOAD_INTERVAL_MIN} min"
        return 0
    fi

    return 1
}

# Main
main() {
    if [ "$SETUP_MODE" = true ]; then
        setup
        exit $?
    fi

    if [ "$CLEANUP_ONLY" = true ]; then
        local folder_id=$(get_folder_id)
        if [ -n "$folder_id" ]; then
            cleanup_old_drive_backups "$folder_id"
        fi
        exit $?
    fi

    if upload_latest; then
        exit 0
    else
        osascript -e "display notification \"FAISS Memory Google Drive backup failed! Check gdrive.log\" with title \"FAISS Memory Backup Alert\"" 2>/dev/null || true
        exit 1
    fi
}

main "$@"
