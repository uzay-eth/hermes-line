"""Attach-only ACP viewer. Bootstrap writes stay in the disposable trial home."""
import os
import sys
from pathlib import Path

sys.dont_write_bytecode = True
source = Path('/Users/mac/.hermes/hermes-agent')
sys.path.insert(0, str(source))
# Dependencies are explicitly pinned by the harness's PYTHONPATH, as in L1.
# Current bootstrap otherwise takes the *owner install's* write lock even with
# a scratch HERMES_HOME. Disable install maintenance in this viewer only. The
# sandbox still refuses every write outside the trial run directory.
from unittest.mock import patch
with patch('hermes_cli.venv_sync.prepare_launch', return_value=None), \
     patch('hermes_cli._early_recovery.recover_if_needed'), \
     patch('pm.environments.activate_dependencies'):
    import hermes_bootstrap  # noqa: E402,F401

profile = os.environ.pop('TRIAL_CALL_PROFILE', '')
if profile != 'hermes-line-calls':
    sys.exit(5)  # never silently fall back to the unrestricted owner profile
os.environ['HERMES_HOME'] = str(Path.home() / '.hermes/profiles' / profile)
os.environ.pop('HERMES_SAFE_MODE', None)
from hermes_cli.gateway_runtime import attachable, discover_gateway_endpoint, gateway_profile_home
from hermes_constants import get_hermes_home

if not attachable(discover_gateway_endpoint(gateway_profile_home(get_hermes_home()))):
    sys.exit(4)  # never invoke ACP's autostart path without a ready owner
if '--check' in sys.argv:
    print('gateway ready (attach only)')
else:
    # Explicit endpoint uses the client's documented attach-only branch, including
    # if the owner disappears between discovery and connection (no autostart).
    from hermes_cli.gateway_client import connect_gateway
    import acp_adapter.gateway_server as server
    found = discover_gateway_endpoint(gateway_profile_home(get_hermes_home()))
    if not attachable(found):
        sys.exit(4)
    server.connect_gateway = lambda: connect_gateway(endpoint=found.endpoint)
    # /tools normally prefers the collapsed visible bridge over complete scope.
    # Supply the unfiltered authoritative metadata so r9 can verify exact identity.
    import acp_adapter.gateway_commands as commands
    from scope import install_scope_projection
    install_scope_projection(commands, os.environ.pop('TRIAL_SCOPE_REPORT', None))
    from acp_adapter.entry import main
    main([])
