#!/bin/sh
set -eu

# Fail early with a readable message if a mounted directory is not writable by this container's user.
for dir in "$MEDIA_DIR" "$LOG_DIR" /models; do
  if [ -n "$dir" ] && [ ! -w "$dir" ]; then
    echo "ERROR: $dir is not writable by uid $(id -u). Fix the host directory permissions (see README)." >&2
    exit 1
  fi
done

if [ "${RUN_MIGRATIONS:-true}" = "true" ]; then
  echo "Applying database migrations..."
  alembic upgrade head
fi

exec "$@"
