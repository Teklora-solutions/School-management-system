#!/bin/sh
set -eu

# Only the one-shot init service runs as root, to prepare bind-mount ownership.
# Never recursively chown the shared data root (PostgreSQL owns its own files).
if [ "$(id -u)" = "0" ]; then
    mkdir -p /data/media /data/static
    chown school:school /data/media /data/static
    exec gosu school "$0" "$@"
fi

exec "$@"
