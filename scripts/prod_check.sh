#!/usr/bin/env bash
# =============================================================================
# Production Readiness & Health Verification Script
# =============================================================================
# Usage:
#   ./scripts/prod_check.sh [port]
# =============================================================================

set -u

PORT="${1:-5860}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

echo "=========================================================="
echo " Faculty Attendance System — Production Readiness Check"
echo " Target Port: $PORT"
echo "=========================================================="

PASS=0
FAIL=0

check() {
  local name="$1"
  local cmd="$2"
  echo -n "Checking $name... "
  if eval "$cmd" > /dev/null 2>&1; then
    echo "✓ [PASS]"
    PASS=$((PASS + 1))
  else
    echo "✗ [FAIL]"
    FAIL=$((FAIL + 1))
  fi
}

# 1. Check Docker status
check "Docker daemon" "docker info"

# 2. Check Database Containers
check "MySQL container running" "docker ps --format '{{.Names}}' | grep -q '^staff_attendance_mysql$'"
check "Redis container running" "docker ps --format '{{.Names}}' | grep -q '^face_recognition_redis$'"
check "Qdrant container running" "docker ps --format '{{.Names}}' | grep -q '^qdrant_db$'"

# 3. Check App Container
check "App container running" "docker ps --format '{{.Names}}' | grep -q '^staff_attendance_app$'"

# 4. Check Health Endpoint
check "API healthz endpoint (HTTP 200)" "curl -sf http://127.0.0.1:${PORT}/healthz"

# 5. Check Web Route
check "Web root endpoint (HTTP 200)" "curl -sf http://127.0.0.1:${PORT}/"
check "About page endpoint (HTTP 200)" "curl -sf http://127.0.0.1:${PORT}/about"
check "Favicon static asset (HTTP 200)" "curl -sf http://127.0.0.1:${PORT}/static/img/image.png"

# 6. Check Directories
check "Media directory exists & writable" "[ -w '${PROJECT_ROOT}/media' ]"
check "Logs directory exists & writable" "[ -w '${PROJECT_ROOT}/logs' ]"

echo "=========================================================="
echo " Summary: $PASS passed, $FAIL failed."
if [ "$FAIL" -eq 0 ]; then
  echo " Status: All checks PASSED! System is production-ready."
  exit 0
else
  echo " Status: $FAIL check(s) failed. Please review the output above."
  exit 1
fi

