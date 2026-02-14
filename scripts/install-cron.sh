#!/bin/bash
#
# Install/uninstall cron jobs for FAISS Memory backups
#
# Jobs:
#   - Backup: snapshot every 30 minutes + hourly Google Drive upload
#
# Usage:
#   ./scripts/install-cron.sh install   # Add cron job
#   ./scripts/install-cron.sh uninstall # Remove cron job
#   ./scripts/install-cron.sh status    # Check if installed
#

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
BACKUP_SCRIPT="$SCRIPT_DIR/backup.sh"
LOG_DIR="$HOME/backups/faiss-memory"

# Cron identifier
CRON_COMMENT="# FAISS Memory backup"

# Run every 30 minutes
BACKUP_SCHEDULE="*/30 * * * *"
BACKUP_LINE="$BACKUP_SCHEDULE $BACKUP_SCRIPT >> $LOG_DIR/cron.log 2>&1 $CRON_COMMENT"

case "$1" in
    install)
        # Ensure scripts are executable
        chmod +x "$SCRIPT_DIR/backup.sh"
        chmod +x "$SCRIPT_DIR/backup-gdrive.sh"

        # Ensure log directory exists
        mkdir -p "$LOG_DIR"

        # Get current crontab
        CURRENT=$(crontab -l 2>/dev/null || true)

        # Add backup job if not present
        if ! echo "$CURRENT" | grep -q "FAISS Memory backup"; then
            UPDATED="$CURRENT
$BACKUP_LINE"
            echo "$UPDATED" | crontab -
            echo "✅ FAISS Memory backup cron job installed"
        else
            echo "⏭️  Cron job already installed"
        fi

        echo ""
        echo "📋 Schedule:"
        echo "   Backup: every 30 min (local snapshot)"
        echo "   GDrive: hourly (throttled, triggered by backup)"
        echo ""
        echo "   Scripts: $SCRIPT_DIR/"
        echo "   Logs:    $LOG_DIR/"
        echo "   Retention: 30 days local, 7 days Google Drive"
        ;;

    uninstall)
        crontab -l 2>/dev/null | grep -v "FAISS Memory backup" | crontab -
        echo "✅ Cron job removed"
        ;;

    status)
        echo "📋 FAISS Memory Backup Status:"
        echo ""

        if crontab -l 2>/dev/null | grep -q "FAISS Memory backup"; then
            echo "✅ Cron: INSTALLED"
            crontab -l | grep "FAISS Memory backup" | sed 's/^/   /'
        else
            echo "❌ Cron: NOT INSTALLED"
        fi
        echo ""

        # Check scripts
        if [ -f "$BACKUP_SCRIPT" ] && [ -x "$BACKUP_SCRIPT" ]; then
            echo "✅ backup.sh: OK"
        else
            echo "⚠️  backup.sh: missing or not executable"
        fi

        if [ -f "$SCRIPT_DIR/backup-gdrive.sh" ] && [ -x "$SCRIPT_DIR/backup-gdrive.sh" ]; then
            echo "✅ backup-gdrive.sh: OK"
        else
            echo "⚠️  backup-gdrive.sh: missing or not executable"
        fi
        echo ""

        # Check logs
        if [ -d "$LOG_DIR" ]; then
            local_count=$(find "$LOG_DIR" -maxdepth 1 -mindepth 1 -type d 2>/dev/null | wc -l | tr -d ' ')
            echo "📊 Local snapshots: $local_count"

            if [ -f "$LOG_DIR/gdrive.log" ]; then
                last_gdrive=$(tail -1 "$LOG_DIR/gdrive.log" 2>/dev/null || echo "none")
                echo "📊 Last GDrive log: $last_gdrive"
            fi
        fi
        ;;

    *)
        echo "Usage: $0 {install|uninstall|status}"
        exit 1
        ;;
esac
