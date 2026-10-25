#!/bin/sh
set -e
# RD_API_TOKEN is optional here: it can be set in the web UI (stored in /config/settings.json)
chown "$PUID:$PGID" /config /data/downloads 2>/dev/null || true
umask "${UMASK:-002}"
# single worker: state lives in-process; threads handle concurrency
exec gosu "$PUID:$PGID" python -m gunicorn -b "0.0.0.0:${PORT:-6500}" -w 1 --threads 8 main:app
