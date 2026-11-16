#!/bin/sh
# Entrypoint of a job container (started by the runner). Reads the Claude credential from the read-only file the
# runner mounted, puts it into the one environment variable claude reads, and writes claude's output to the job's
# files in the work tree.
set -eu
if [ -n "${CUTLERY_SECRET_VAR:-}" ] && [ -r /run/secrets/claude ]; then
  export "$CUTLERY_SECRET_VAR=$(cat /run/secrets/claude)"
fi
mkdir -p "${TMPDIR:-/tmp}" "${HOME:-/tmp}" 2>/dev/null || true
exec "$@" >>"${CUTLERY_JOB_STDOUT:-/dev/stdout}" 2>>"${CUTLERY_JOB_STDERR:-/dev/stderr}"
