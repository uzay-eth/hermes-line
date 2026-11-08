#!/bin/sh
# Guarded real-model latency run against the owner's live Hermes home (coordinator decision, L1 round 2).
# One run, at most 14 turns, no retries. Every file write outside $S/run is denied by sandbox-exec,
# so nothing can be written under ~/.hermes; the before/after shasum and the sandbox's own deny log
# are the evidence. Mac-specific paths; prepared as described in docs/lanes/L1.md section 4.
set -u
S=/Users/mac/.hermes/cache/scratch/hermes-line-l1
REPO=$(cd "$(dirname "$0")/../../.." && pwd)
LIVE=/Users/mac/.hermes
OUT=$S/run/live
mkdir -p "$OUT"
hashes() { (cd "$LIVE" && shasum -a 256 config.yaml auth.json state.db; ls -la state.db-wal state.db-shm 2>/dev/null | awk '{print $5, $6, $7, $8, $9}'); }
echo "== before $(date -u +%FT%TZ)"; hashes | tee "$OUT/hash-before.txt"
SANDBOX="/usr/bin/sandbox-exec -f $S/l1-ro.sb"
echo "== guard"
env -i PATH="/usr/bin:/bin" HOME="$HOME" HERMES_HOME="$LIVE" $SANDBOX /usr/bin/python3 -B "$REPO/tests/l1/bench/guard_credentials.py" \
  | tee "$OUT/guard.json" || { echo "GUARD FAILED: not running"; exit 2; }
echo "== run (embedded driver, 14 turns, no retry) $(date -u +%FT%TZ)"
START=$(date "+%Y-%m-%d %H:%M:%S")
(cd "$S/run" && env -i PATH="/usr/bin:/bin:/usr/sbin:/sbin" HOME="$HOME" HERMES_HOME="$S/run/boothome" \
  HERMES_DATA_DIR_SUFFIX=-l1-boot TARGET_HERMES_HOME="$LIVE" HERMES_SAFE_MODE=1 HERMES_DISABLE_LAZY_INSTALLS=1 \
  HERMES_RUNTIME_DIR="$S/run/tools" TMPDIR="$S/run/tmp" PYTHONDONTWRITEBYTECODE=1 \
  HERMES_AGENT_ROOT=/Users/mac/.hermes/hermes-agent \
  $SANDBOX "$S/run/venv/bin/python" -B -W ignore "$S/run_live.py" "$REPO/tests/l1/bench/latency_real_model.py" \
  "$OUT/latency-embedded.json" --driver embedded --max-turns 14) > "$OUT/run.log" 2>&1
echo "run exit=$?"
sleep 2
echo "== after $(date -u +%FT%TZ)"; hashes | tee "$OUT/hash-after.txt"
if diff -q "$OUT/hash-before.txt" "$OUT/hash-after.txt" >/dev/null; then echo "HASHES UNCHANGED"; else echo "HASHES CHANGED"; diff "$OUT/hash-before.txt" "$OUT/hash-after.txt"; fi
echo "== sandbox denied writes during the run"
/usr/bin/log show --start "$START" --style compact --predicate 'eventMessage CONTAINS "deny" AND eventMessage CONTAINS "file-write"' 2>/dev/null \
  | grep -i "python" | sed -E 's/^.*(deny\([0-9]+\) file-write[^ ]* )/\1/' | sort | uniq -c | sort -rn | head -40 > "$OUT/denied-writes.txt"
cat "$OUT/denied-writes.txt"
