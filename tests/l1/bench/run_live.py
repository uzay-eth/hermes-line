"""Run an L1 bench against TARGET_HERMES_HOME while Hermes's bootstrap bookkeeping stays in scratch.

hermes_bootstrap opens the install lock under <default root>/installs for writing. It runs first with
HERMES_HOME = the scratch boot home, so that lock lives in scratch; then HERMES_HOME is switched to
the target (read live by hermes_constants) before the bench reads any config or credential.
"""
import os, runpy, sys
sys.dont_write_bytecode = True
sys.path.insert(0, '/Users/mac/.hermes/hermes-agent')
sys.path.insert(0, '/Users/mac/.hermes/installs/778930d1ee3bddda/environments/36f5841acd484742aba00d117c04db01/venv/lib/python3.14/site-packages')
assert os.environ['HERMES_HOME'].startswith('/Users/mac/.hermes/cache/scratch/hermes-line-l1/run/')
import hermes_bootstrap  # noqa: F401  (install bookkeeping in the scratch boot home)
target = os.environ.pop('TARGET_HERMES_HOME')
os.environ.pop('HERMES_DATA_DIR_SUFFIX', None)
os.environ['HERMES_HOME'] = target
# Harness-only: Hermes file logs (agent.log, errors.log) go to a scratch log home, because the run
# must write nothing into the target home. The plugin itself never does this.
from pathlib import Path
import hermes_logging
_orig_setup_logging = hermes_logging.setup_logging
LOG_HOME = Path(os.environ.pop('L1_LOG_HOME', '/Users/mac/.hermes/cache/scratch/hermes-line-l1/run/loghome'))
def _scratch_logging(**kw):
    kw['hermes_home'] = LOG_HOME
    return _orig_setup_logging(**kw)
hermes_logging.setup_logging = _scratch_logging
from hermes_constants import get_hermes_home
print('hermes home for the run:', get_hermes_home(), file=sys.stderr)
script = sys.argv.pop(1)
runpy.run_path(script, run_name='__main__')
