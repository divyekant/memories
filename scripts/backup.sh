#!/bin/bash
#
# FAISS Memory - Periodic Backup
# Triggers a backup via the API and saves a local snapshot
#
# Usage:
#   ./scripts/backup.sh              # Run backup
#   ./scripts/backup.sh --test       # Dry run
#   ./scripts/backup.sh --cleanup    # Only run cleanup
#
# Environment:
#   FAISS_API_KEY     - API key for the FAISS service (required if auth enabled)
#   FAISS_URL         - Service URL (default: http://localhost:8900)
#   FAISS_DATA_DIR    - Path to Docker volume data dir (default: ./data relative to repo root)
#   BACKUP_DIR        - Where to store snapshots (default: ~/backups/faiss-memory)
#   RETENTION_DAYS    - Days to keep local snapshots (default: 30)
#

set -e

# Ensure tools are in PATH (cron has minimal PATH)
export PATH="/usr/local/bin:/opt/homebrew/bin:$PATH"

# Source env vars (cron doesn't load shell profile)
[ -f "$HOME/.zshrc" ] && source "$HOME/.zshrc" 2>/dev/null || true

# Resolve paths
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

# Configuration (all overridable via env vars)
BACKUP_DIR="${BACKUP_DIR:-$HOME/backups/faiss-memory}"
LOG_FILE="$BACKUP_DIR/backup.log"
FAISS_URL="${FAISS_URL:-http://localhost:8900}"
API_KEY="${FAISS_API_KEY}"
FAISS_DATA_DIR="${FAISS_DATA_DIR:-$PROJECT_DIR/data}"
RETENTION_DAYS="${RETENTION_DAYS:-30}"

# Parse arguments
TEST_MODE=false
CLEANUP_ONLY=false
for arg in "$@"; do
    case $arg in
        --test) TEST_MODE=true ;;
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

# Create local backup snapshot (copy from Docker volume)
create_local_snapshot() {
    local timestamp=$(date +"%Y-%m-%d-%H-%M-%S")
    local snapshot_dir="$BACKUP_DIR/$timestamp"

    log "INFO" "Creating local snapshot: $timestamp"

    if [ "$TEST_MODE" = true ]; then
        log "INFO" "[DRY RUN] Would create snapshot: $snapshot_dir"
        return 0
    fi

    # Trigger backup via API
    local response=$(curl -s -w "\n%{http_code}" \
        -X POST "$FAISS_URL/backup?prefix=scheduled" \
        -H "X-API-Key: $API_KEY" 2>/dev/null)

    local http_code=$(echo "$response" | tail -1)
    local body=$(echo "$response" | head -1)

    if [ "$http_code" != "200" ]; then
        log "ERROR" "API backup failed (HTTP $http_code): $body"
        return 1
    fi

    # Copy the data files to our local snapshot
    if [ ! -d "$FAISS_DATA_DIR" ]; then
        log "ERROR" "Data directory not found: $FAISS_DATA_DIR"
        return 1
    fi

    mkdir -p "$snapshot_dir"
    cp -f "$FAISS_DATA_DIR/index.faiss" "$snapshot_dir/" 2>/dev/null || true
    cp -f "$FAISS_DATA_DIR/metadata.json" "$snapshot_dir/" 2>/dev/null || true
    cp -f "$FAISS_DATA_DIR/config.json" "$snapshot_dir/" 2>/dev/null || true

    local size=$(du -sh "$snapshot_dir" 2>/dev/null | cut -f1)
    log "SUCCESS" "Snapshot created: $timestamp ($size)"
    return 0
}

# Cleanup old snapshots
cleanup_old_snapshots() {
    log "INFO" "Cleaning up snapshots older than $RETENTION_DAYS days..."

    local deleted=0
    while IFS= read -r -d '' dir; do
        if [ "$TEST_MODE" = true ]; then
            log "INFO" "[DRY RUN] Would delete: $(basename "$dir")"
        else
            rm -rf "$dir"
            deleted=$((deleted + 1))
            log "INFO" "Deleted old snapshot: $(basename "$dir")"
        fi
    done < <(find "$BACKUP_DIR" -maxdepth 1 -mindepth 1 -type d -mtime +$RETENTION_DAYS -print0 2>/dev/null)

    if [ "$deleted" -gt 0 ]; then
        log "SUCCESS" "Cleaned up $deleted old snapshot(s)"
    else
        log "INFO" "No old snapshots to clean up"
    fi
}

# Track consecutive failures for alerting
FAIL_COUNTER_FILE="$BACKUP_DIR/.fail_count"

record_success() {
    echo "0" > "$FAIL_COUNTER_FILE"
}

record_failure() {
    local count=0
    if [ -f "$FAIL_COUNTER_FILE" ]; then
        count=$(cat "$FAIL_COUNTER_FILE" 2>/dev/null || echo "0")
    fi
    count=$((count + 1))
    echo "$count" > "$FAIL_COUNTER_FILE"

    # Alert after 3 consecutive failures
    if [ "$count" -ge 3 ]; then
        log "ERROR" "ALERT: $count consecutive backup failures!"
        osascript -e "display notification \"$count consecutive FAISS backup failures! Check backup.log\" with title \"FAISS Memory Backup Alert\"" 2>/dev/null || true
    fi
}

# Main
main() {
    mkdir -p "$BACKUP_DIR"

    if [ "$CLEANUP_ONLY" = true ]; then
        cleanup_old_snapshots
        exit $?
    fi

    if create_local_snapshot; then
        record_success
        cleanup_old_snapshots

        local total=$(find "$BACKUP_DIR" -maxdepth 1 -mindepth 1 -type d 2>/dev/null | wc -l | tr -d ' ')
        log "SUCCESS" "Backup complete. Total snapshots: $total"

        # Upload to Google Drive if configured (optional, non-blocking)
        if [ -f "$SCRIPT_DIR/backup-gdrive.sh" ] && [ -n "$GDRIVE_ACCOUNT" ]; then
            bash "$SCRIPT_DIR/backup-gdrive.sh" >> "$LOG_FILE" 2>&1 || log "ERROR" "Google Drive upload failed (local backup OK)"
        fi

        exit 0
    else
        record_failure
        log "ERROR" "Backup failed"
        exit 1
    fi
}

main "$@"
