#!/bin/sh
# Runs as PUID:PGID from .env (compose `user:`). Some tools need a passwd entry for that UID.
if ! whoami >/dev/null 2>&1; then
  echo "cutlery:x:$(id -u):$(id -g):cutlery:${HOME:-/home/cutter}:/bin/sh" >> /etc/passwd 2>/dev/null || true
fi
# temporary files of the app and of jobs go to the work tree, never into the container layer
mkdir -p "${TMPDIR:-/work/tmp}" 2>/dev/null || true
exec cutlery "$@"
