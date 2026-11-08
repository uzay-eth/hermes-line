#!/bin/sh
# Offline, stdlib-only. From anywhere: tests/l4/run_tests.sh [-v] [other unittest args]
# PYTHON=/path/to/python3.11 tests/l4/run_tests.sh   to pick the interpreter.
cd "$(dirname "$0")/.." || exit 1
exec "${PYTHON:-python3}" -m unittest discover -s l4 -t . "$@"
