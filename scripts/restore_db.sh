#!/usr/bin/env bash
# =============================================================================
# Database Restore Script for Faculty Attendance System
# =============================================================================
# Usage:
#   ./scripts/restore_db.sh <path_to_backup_file.sql.gz>
# =============================================================================

set -euo pipefail

if [ "$#" -ne 1 ]; then
  echo "Usage: $0 <path_to_backup_file.sql.gz>" >&2
  exit 1
fi

BACKUP_FILE="$1"

if [ ! -f "$BACKUP_FILE" ]; then
  echo "ERROR: Backup file '$BACKUP_FILE' does not exist." >&2
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
ENV_FILE="${PROJECT_ROOT}/.env"

MYSQL_ROOT_PW=$(grep -E '^MYSQL_ROOT_PASSWORD=' "$ENV_FILE" | cut -d '=' -f2- | tr -d '"' | tr -d "'")
MYSQL_DB=$(grep -E '^MYSQL_DATABASE=' "$ENV_FILE" | cut -d '=' -f2- | tr -d '"' | tr -d "'" || echo "staff_attendance")

echo "WARNING: This will overwrite data in database '${MYSQL_DB}'!"
read -p "Are you sure you want to proceed? (yes/no): " CONFIRM
if [ "$CONFIRM" != "yes" ]; then
  echo "Restore cancelled."
  exit 0
fi

echo "[$(date '+%Y-%m-%d %H:%M:%S')] Restoring database from $BACKUP_FILE..."

gunzip -c "$BACKUP_FILE" | docker exec -i staff_attendance_mysql mysql -u root -p"${MYSQL_ROOT_PW}" "$MYSQL_DB"

echo "[$(date '+%Y-%m-%d %H:%M:%S')] Database restore completed successfully."

