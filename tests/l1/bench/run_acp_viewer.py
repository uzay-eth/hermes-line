"""`hermes acp` viewer for the L1 latency runs, with Hermes's bootstrap bookkeeping kept in scratch.

1. hermes_bootstrap runs with HERMES_HOME = L1_VIEWER_BOOT (scratch), so its install lock is in scratch.
2. HERMES_HOME switches to L1_VIEWER_TARGET; the viewer refuses to continue unless a ready gateway
   owner already serves that home (it must attach, never start one).
3. HERMES_SAFE_MODE is removed for the viewer only: on the ACP path it would freeze a safe-mode session
   (no profile config, plugins or memory), which is not what production ACP runs.
"""
import os, sys
sys.dont_write_bytecode = True
boot, target = os.environ.pop("L1_VIEWER_BOOT"), os.environ.pop("L1_VIEWER_TARGET")
os.environ["HERMES_HOME"] = boot
os.environ["HERMES_DATA_DIR_SUFFIX"] = "-l1-viewer-boot"
import hermes_bootstrap  # noqa: F401
os.environ.pop("HERMES_DATA_DIR_SUFFIX", None)
os.environ["HERMES_HOME"] = target
os.environ.pop("HERMES_SAFE_MODE", None)
import json
from hermes_cli.gateway_runtime import _owner_pid, attachable, discover_gateway_endpoint, gateway_profile_home
from hermes_constants import get_hermes_home
_home = gateway_profile_home(get_hermes_home())
_found = discover_gateway_endpoint(_home)
_report = {"gateway_home": str(_home), "state": _found.state, "owner_pid": _owner_pid(_home),
           "attachable": attachable(_found), "safe_mode_env": os.environ.get("HERMES_SAFE_MODE")}
if os.environ.get("L1_VIEWER_REPORT"):
    with open(os.environ.pop("L1_VIEWER_REPORT"), "a") as fh:
        fh.write(json.dumps(_report) + "\n")
if not _report["attachable"]:
    print("no ready gateway owner for the target home; refusing to start one", file=sys.stderr)
    sys.exit(4)
from acp_adapter.entry import main
main([])
