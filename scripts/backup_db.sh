#!/usr/bin/env bash
# =============================================================================
# Automated Database Backup Script for Faculty Attendance System
# =============================================================================
# Usage:
#   ./scripts/backup_db.sh
# Can be scheduled via cron (e.g. daily at 2 AM):
#   0 2 * * * /path/to/Staff_attendence_system/scripts/backup_db.sh >> /var/log/db_backup.log 2>&1
# =============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
BACKUP_DIR="${PROJECT_ROOT}/backups"
TIMESTAMP="$(date +'%Y%m%d_%H%M%S')"
BACKUP_FILE="${BACKUP_DIR}/attendance_backup_${TIMESTAMP}.sql.gz"
RETENTION_DAYS=14

mkdir -p "$BACKUP_DIR"

echo "[$(date '+%Y-%m-%d %H:%M:%S')] Starting MySQL database backup..."

# Extract DB credentials from .env if available
ENV_FILE="${PROJECT_ROOT}/.env"
if [ -f "$ENV_FILE" ]; then
  MYSQL_ROOT_PW=$(grep -E '^MYSQL_ROOT_PASSWORD=' "$ENV_FILE" | cut -d '=' -f2- | tr -d '"' | tr -d "'")
  MYSQL_DB=$(grep -E '^MYSQL_DATABASE=' "$ENV_FILE" | cut -d '=' -f2- | tr -d '"' | tr -d "'" || echo "staff_attendance")
fi

MYSQL_DB="${MYSQL_DB:-staff_attendance}"

if docker ps --format '{{.Names}}' | grep -q "^staff_attendance_mysql$"; then
  echo "Dumping from Docker container 'staff_attendance_mysql'..."
  docker exec staff_attendance_mysql mysqldump \
    -u root -p"${MYSQL_ROOT_PW}" \
    --single-transaction \
    --quick \
    --routines \
    --triggers \
    "$MYSQL_DB" | gzip > "$BACKUP_FILE"
else
  echo "ERROR: Container 'staff_attendance_mysql' is not running!" >&2
  exit 1
fi

FILE_SIZE=$(du -h "$BACKUP_FILE" | cut -f1)
echo "[$(date '+%Y-%m-%d %H:%M:%S')] Backup successfully created: $BACKUP_FILE ($FILE_SIZE)"

# Prune backups older than RETENTION_DAYS
echo "Pruning backups older than ${RETENTION_DAYS} days..."
find "$BACKUP_DIR" -name "attendance_backup_*.sql.gz" -type f -mtime +"$RETENTION_DAYS" -delete

echo "[$(date '+%Y-%m-%d %H:%M:%S')] Backup completed successfully."

