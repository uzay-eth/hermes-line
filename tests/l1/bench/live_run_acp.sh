#!/bin/sh
# ONE guarded live ACP latency run on the owner's route (coordinator decision, L1 round 3).
# The bench and the `hermes acp` viewer are sandboxed (no file write outside $OUT); the viewer only
# attaches to the already-running gateway of ~/.hermes, which saves this one session (titled below).
set -u
S=/Users/mac/.hermes/cache/scratch/hermes-line-l1
REPO=$(cd "$(dirname "$0")/../../.." && pwd)
LIVE=/Users/mac/.hermes
OUT=$S/run/live-acp
TITLE="hermes-line latency test 2026-11-05 L1 round 3"
SP=/Users/mac/.hermes/installs/778930d1ee3bddda/environments/36f5841acd484742aba00d117c04db01/venv/lib/python3.14/site-packages
rm -rf "$OUT"; mkdir -p "$OUT"
hashes() { (cd "$LIVE" && shasum -a 256 config.yaml auth.json;
  find memories -type f 2>/dev/null | sort | xargs shasum -a 256 2>/dev/null | shasum -a 256 | sed 's/-$/memories (digest of all files)/'); }
owner() { env -i PATH="/usr/bin:/bin" HOME="$HOME" HERMES_HOME="$LIVE" TMPDIR="$OUT" PYTHONPATH="/Users/mac/.hermes/hermes-agent:$SP" \
  /usr/bin/sandbox-exec -f "$S/l1-ro.sb" "$S/run/venv/bin/python" -B -W ignore -c "
from hermes_cli.gateway_runtime import _owner_pid, discover_gateway_endpoint, attachable, gateway_profile_home
from hermes_constants import get_hermes_home
h = gateway_profile_home(get_hermes_home()); r = discover_gateway_endpoint(h)
print(_owner_pid(h) if attachable(r) else 'none')" 2>/dev/null | tail -1; }
children() { [ "$1" = none ] && return; pgrep -P "$1" | while read p; do ps -o pid=,command= -p "$p" | cut -c1-120; done; }
echo "== before $(date -u +%FT%TZ)"; hashes | tee "$OUT/hash-before.txt"
GW=$(owner); echo "gateway owner pid: $GW" | tee "$OUT/gateway.txt"; children "$GW" > "$OUT/children-before.txt"; cat "$OUT/children-before.txt"
[ "$GW" = none ] && { echo "NO READY GATEWAY: not running"; exit 3; }
echo "== guard"
env -i PATH="/usr/bin:/bin" HOME="$HOME" HERMES_HOME="$LIVE" /usr/bin/sandbox-exec -f "$S/l1-ro.sb" /usr/bin/python3 -B \
  "$REPO/tests/l1/bench/guard_credentials.py" | tee "$OUT/guard.json" || { echo "GUARD FAILED: not running"; exit 2; }
echo "== run (ACP driver, 14 turns, no retry) $(date -u +%FT%TZ)"
START=$(date "+%Y-%m-%d %H:%M:%S")
sh "$REPO/tests/l1/bench/acp_session_run.sh" "$LIVE" "$OUT" "$TITLE" 14
sleep 2
echo "== after $(date -u +%FT%TZ)"; hashes | tee "$OUT/hash-after.txt"
children "$GW" > "$OUT/children-after.txt"; cat "$OUT/children-after.txt"
if diff -q "$OUT/hash-before.txt" "$OUT/hash-after.txt" >/dev/null; then echo "HASHES UNCHANGED"; else echo "HASHES CHANGED"; diff "$OUT/hash-before.txt" "$OUT/hash-after.txt"; fi
/usr/bin/log show --start "$START" --style compact --predicate 'eventMessage CONTAINS "deny" AND eventMessage CONTAINS "file-write"' 2>/dev/null \
  | grep -i python | head -40 > "$OUT/denied-writes.txt"; echo "denied writes logged: $(grep -c . "$OUT/denied-writes.txt")"
cat "$OUT/viewer.jsonl"
