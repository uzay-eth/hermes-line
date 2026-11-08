#!/bin/sh
# The sandbox enforces scratch-only writes and loopback-only networking.
set -eu
S1_ROOT=/Users/mac/.hermes/cache/scratch/hermes-line-s1
exec env -i PATH="$PATH" HERMES_HOME="$S1_ROOT" HERMES_DATA_DIR_SUFFIX=-s1-isolation \
  HERMES_DISABLE_LAZY_INSTALLS=1 HERMES_RUNTIME_DIR="$S1_ROOT/tools" \
  TMPDIR="$S1_ROOT/tmp" PYTHONDONTWRITEBYTECODE=1 \
  /usr/bin/sandbox-exec -f "$S1_ROOT/offline.sb" "$S1_ROOT/venv/bin/python" -B \
  spikes/s1-proto/run.py "$@"
