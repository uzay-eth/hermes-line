#!/bin/sh
# Offline, stdlib-only. From anywhere: spikes/s3-sms/run_tests.sh
cd "$(dirname "$0")" || exit 1
exec python3 -m unittest discover -s tests -t . -v "$@"
