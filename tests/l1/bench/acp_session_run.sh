#!/bin/sh
# One ACP latency run: bench (sandboxed, HERMES_SAFE_MODE=1, writes only under $OUT) drives a call whose
# Hermes session is a `hermes acp` viewer attached to TARGET's already-running gateway.
# Usage: acp_session_run.sh TARGET_HOME OUT_DIR TITLE MAX_TURNS
set -u
S=/Users/mac/.hermes/cache/scratch/hermes-line-l1
REPO=$(cd "$(dirname "$0")/../../.." && pwd)
TARGET=$1; OUT=$2; TITLE=$3; TURNS=$4
SP=/Users/mac/.hermes/installs/778930d1ee3bddda/environments/36f5841acd484742aba00d117c04db01/venv/lib/python3.14/site-packages
mkdir -p "$OUT/tmp" "$OUT/loghome" "$OUT/boothome" "$OUT/viewerboot"
cat > "$OUT/sandbox.sb" <<SB
(version 1)
(allow default)
(deny file-write* (require-not (subpath "$OUT")))
(allow file-write* (literal "/dev/null") (literal "/dev/tty") (literal "/dev/dtracehelper"))
SB
cd "$OUT" && env -i PATH="/usr/bin:/bin:/usr/sbin:/sbin" HOME="$HOME" HERMES_HOME="$OUT/boothome" \
  HERMES_DATA_DIR_SUFFIX=-l1-boot TARGET_HERMES_HOME="$TARGET" HERMES_SAFE_MODE=1 HERMES_DISABLE_LAZY_INSTALLS=1 \
  HERMES_RUNTIME_DIR="$S/run/tools" TMPDIR="$OUT/tmp" PYTHONDONTWRITEBYTECODE=1 L1_LOG_HOME="$OUT/loghome" \
  PYTHONPATH="/Users/mac/.hermes/hermes-agent:$SP" HERMES_AGENT_ROOT=/Users/mac/.hermes/hermes-agent \
  L1_VIEWER_BOOT="$OUT/viewerboot" L1_VIEWER_TARGET="$TARGET" L1_VIEWER_REPORT="$OUT/viewer.jsonl" L1_ACP_TITLE="$TITLE" \
  L1_ACP_COMMAND="[\"$S/run/venv/bin/python\", \"-B\", \"$REPO/tests/l1/bench/run_acp_viewer.py\"]" \
  /usr/bin/sandbox-exec -f "$OUT/sandbox.sb" "$S/run/venv/bin/python" -B -W ignore "$REPO/tests/l1/bench/run_live.py" \
  "$REPO/tests/l1/bench/latency_real_model.py" "$OUT/latency-acp.json" --driver acp --max-turns "$TURNS" > "$OUT/run.log" 2>&1
echo "run exit=$?"
