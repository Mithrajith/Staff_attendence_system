#!/usr/bin/env bash
set -e

# Change directory to project root
cd "$(dirname "$0")"

# Check if Docker app container is occupying port 5860
if docker ps --format '{{.Names}}' 2>/dev/null | grep -q "^staff_attendance_app$"; then
  echo "Stopping docker app container 'staff_attendance_app' so port 5860 is free for manual run..."
  docker stop staff_attendance_app
fi

# Ensure database containers are up
for svc in staff_attendance_mysql face_recognition_redis qdrant_db; do
  if ! docker ps --format '{{.Names}}' 2>/dev/null | grep -q "^$svc$"; then
    echo "Starting dependency container '$svc'..."
    docker start "$svc" 2>/dev/null || true
  fi
done

# Detect container IPs for databases
MYSQL_IP=$(docker inspect staff_attendance_mysql --format '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' 2>/dev/null || echo "127.0.0.1")
REDIS_IP=$(docker inspect face_recognition_redis --format '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' 2>/dev/null || echo "127.0.0.1")
QDRANT_IP=$(docker inspect qdrant_db --format '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' 2>/dev/null || echo "127.0.0.1")

export MYSQL_HOST="${MYSQL_IP:-127.0.0.1}"
export REDIS_URL="redis://${REDIS_IP:-127.0.0.1}:6379/0"
export QDRANT_URL="http://${QDRANT_IP:-127.0.0.1}:6333"
export FRONTEND_URL="http://localhost:5860"

echo "=========================================================="
echo " Starting FastAPI (Manual Mode) on port 5860"
echo "  URL:      http://localhost:5860"
echo "  MySQL:    $MYSQL_HOST:3306"
echo "  Redis:    $REDIS_URL"
echo "  Qdrant:   $QDRANT_URL"
echo "=========================================================="

exec .venv/bin/uvicorn api.main:app --host 0.0.0.0 --port 5860 --reload

