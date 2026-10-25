#!/bin/sh
set -e
if [ -z "$RD_API_TOKEN" ]; then echo "RD_API_TOKEN is required" >&2; exit 1; fi
chown "$PUID:$PGID" /config /downloads 2>/dev/null || true
# single worker: state lives in-process; threads handle concurrency
exec gosu "$PUID:$PGID" python -m gunicorn -b "0.0.0.0:${PORT:-8080}" -w 1 --threads 8 main:app
