#!/bin/sh
# Driver-overhead comparison on the same local fixture model (no real model, loopback only).
S=/Users/mac/.hermes/cache/scratch/hermes-line-l1; W=/Users/mac/.bb/plugins/environment-git-worktree/host-data/worktrees/thr_adzuzxvfdk-1/hermes-line; PORT=51300
SP=/Users/mac/.hermes/installs/778930d1ee3bddda/environments/36f5841acd484742aba00d117c04db01/venv/lib/python3.14/site-packages
for drv in acp embedded; do
  H=$S/run/cmp-$drv; rm -rf $H; mkdir -p $H
  printf "model:\n  default: l1-fixture\n  provider: custom\n  base_url: http://127.0.0.1:$PORT/v1\n  api_key: offline-fixture\n" > $H/config.yaml
  (cd $S/run && env -i PATH="/usr/bin:/bin:/usr/sbin:/sbin" HOME="$HOME" HERMES_HOME=$H HERMES_DATA_DIR_SUFFIX=-l1-cmp     HERMES_DISABLE_LAZY_INSTALLS=1 HERMES_RUNTIME_DIR=$S/run/tools TMPDIR=$S/run/tmp PYTHONDONTWRITEBYTECODE=1     PYTHONPATH="/Users/mac/.hermes/hermes-agent:$SP" HERMES_AGENT_ROOT=/Users/mac/.hermes/hermes-agent     L1_ACP_COMMAND="[\"$S/run/venv/bin/python\", \"-B\", \"-c\", \"import hermes_bootstrap; from acp_adapter.entry import main; main()\"]"     /usr/bin/sandbox-exec -f $S/l1-ro-offline.sb $S/run/venv/bin/python -B -W ignore     $W/tests/l1/bench/latency_real_model.py $S/run/cmp-$drv.json --driver $drv --max-turns 14) > $S/run/cmp-$drv.log 2>&1
  echo "$drv exit=$?"
done
