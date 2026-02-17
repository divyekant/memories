#!/bin/bash
#
# Install/uninstall cron jobs for Memories backups
#
# Jobs:
#   - Local snapshot every 30 minutes
#   - Google Drive upload (optional, triggered by backup if GDRIVE_ACCOUNT is set)
#
# Usage:
#   ./scripts/install-cron.sh install   # Add cron job
#   ./scripts/install-cron.sh uninstall # Remove cron job
#   ./scripts/install-cron.sh status    # Check if installed
#
# Prerequisites:
#   - Docker running with faiss-memory container
#   - FAISS_API_KEY set in shell profile (if auth enabled)
#
# Optional (for Google Drive off-site backups):
#   - Install gog CLI: https://github.com/skratchdot/gog
#   - Authenticate: gog auth add your-email@gmail.com --services drive
#   - Set env var: export GDRIVE_ACCOUNT="your-email@gmail.com"
#

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
BACKUP_SCRIPT="$SCRIPT_DIR/backup.sh"
LOG_DIR="${BACKUP_DIR:-$HOME/backups/faiss-memory}"

# Cron identifier
CRON_COMMENT="# Memories backup"

# Run every 30 minutes
BACKUP_SCHEDULE="*/30 * * * *"
BACKUP_LINE="$BACKUP_SCHEDULE $BACKUP_SCRIPT >> $LOG_DIR/cron.log 2>&1 $CRON_COMMENT"

case "$1" in
    install)
        # Ensure scripts are executable
        chmod +x "$SCRIPT_DIR/backup.sh"
        [ -f "$SCRIPT_DIR/backup-gdrive.sh" ] && chmod +x "$SCRIPT_DIR/backup-gdrive.sh"

        # Ensure log directory exists
        mkdir -p "$LOG_DIR"

        # Get current crontab
        CURRENT=$(crontab -l 2>/dev/null || true)

        # Add backup job if not present
        if ! echo "$CURRENT" | grep -q "Memories backup"; then
            UPDATED="$CURRENT
$BACKUP_LINE"
            echo "$UPDATED" | crontab -
            echo "Installed FAISS Memory backup cron job"
        else
            echo "Cron job already installed"
        fi

        echo ""
        echo "Schedule:"
        echo "  Backup: every 30 min (local snapshot)"
        if [ -n "$GDRIVE_ACCOUNT" ]; then
            echo "  GDrive: hourly (throttled, triggered by backup)"
            echo "  GDrive account: $GDRIVE_ACCOUNT"
        else
            echo "  GDrive: not configured (set GDRIVE_ACCOUNT to enable)"
        fi
        echo ""
        echo "  Scripts:   $SCRIPT_DIR/"
        echo "  Snapshots: $LOG_DIR/"
        echo "  Retention: ${RETENTION_DAYS:-30} days local"
        [ -n "$GDRIVE_ACCOUNT" ] && echo "             ${GDRIVE_RETENTION_DAYS:-7} days Google Drive"
        ;;

    uninstall)
        crontab -l 2>/dev/null | grep -v "Memories backup" | crontab -
        echo "Cron job removed"
        ;;

    status)
        echo "Memories Backup Status"
        echo "=========================="
        echo ""

        if crontab -l 2>/dev/null | grep -q "Memories backup"; then
            echo "Cron: INSTALLED"
            crontab -l | grep "Memories backup" | sed 's/^/  /'
        else
            echo "Cron: NOT INSTALLED"
        fi
        echo ""

        # Check scripts
        if [ -f "$BACKUP_SCRIPT" ] && [ -x "$BACKUP_SCRIPT" ]; then
            echo "backup.sh: OK"
        else
            echo "backup.sh: missing or not executable"
        fi

        if [ -f "$SCRIPT_DIR/backup-gdrive.sh" ] && [ -x "$SCRIPT_DIR/backup-gdrive.sh" ]; then
            echo "backup-gdrive.sh: OK"
        else
            echo "backup-gdrive.sh: not found (optional)"
        fi
        echo ""

        # GDrive status
        if [ -n "$GDRIVE_ACCOUNT" ]; then
            echo "Google Drive: CONFIGURED ($GDRIVE_ACCOUNT)"
        else
            echo "Google Drive: not configured (set GDRIVE_ACCOUNT to enable)"
        fi
        echo ""

        # Check snapshots
        if [ -d "$LOG_DIR" ]; then
            local_count=$(find "$LOG_DIR" -maxdepth 1 -mindepth 1 -type d -name "20*" 2>/dev/null | wc -l | tr -d ' ')
            echo "Local snapshots: $local_count"

            if [ -f "$LOG_DIR/gdrive.log" ]; then
                last_gdrive=$(tail -1 "$LOG_DIR/gdrive.log" 2>/dev/null || echo "none")
                echo "Last GDrive log: $last_gdrive"
            fi
        fi
        ;;

    *)
        echo "Usage: $0 {install|uninstall|status}"
        exit 1
        ;;
esac
