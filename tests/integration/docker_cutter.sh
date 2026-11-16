#!/bin/sh
# `cutter` for the integration tests: runs the given cutter image (and checkout) in Docker with the test's storage
# trees mounted at the same paths. Set by the tests: CUTLERY_TEST_CUTTER_IMAGE, CUTLERY_TEST_MOUNTS (colon-separated),
# optional CUTLERY_TEST_CUTTER_SRC.
set -eu
args="--rm --user $(id -u):$(id -g) -e CUTTER_PROJECT=${CUTTER_PROJECT} -e PYTHONDONTWRITEBYTECODE=1 -w $(pwd)"
old_ifs=$IFS; IFS=:
for m in $CUTLERY_TEST_MOUNTS; do args="$args -v $m:$m"; done
IFS=$old_ifs
if [ -n "${CUTLERY_TEST_CUTTER_SRC:-}" ]; then
  args="$args -v $CUTLERY_TEST_CUTTER_SRC:/repo:ro -e PYTHONPATH=/repo/src"
fi
# shellcheck disable=SC2086
exec docker run $args --entrypoint python "$CUTLERY_TEST_CUTTER_IMAGE" -m cutter "$@"
