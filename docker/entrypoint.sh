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

# Detect SSL certificates and enable HTTPS on Uvicorn if available and not disabled
if [ "$#" -ge 2 ] && [ "$1" = "uvicorn" ] && [ "${SSL_ENABLED:-true}" != "false" ]; then
  SSL_KEY="${SSL_KEYFILE:-/ssl/server.key}"
  SSL_CERT="${SSL_CERTFILE:-/ssl/server.crt}"
  if [ -f "$SSL_KEY" ] && [ -f "$SSL_CERT" ]; then
    has_ssl=false
    for arg in "$@"; do
      case "$arg" in
        --ssl-keyfile*|--ssl-certfile*) has_ssl=true ;;
      esac
    done
    if [ "$has_ssl" = "false" ]; then
      echo "SSL certificate detected ($SSL_CERT). Launching Uvicorn with HTTPS..."
      set -- "$@" "--ssl-keyfile" "$SSL_KEY" "--ssl-certfile" "$SSL_CERT"
    fi
  fi
fi

exec "$@"
