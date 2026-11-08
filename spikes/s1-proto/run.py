"""Run a probe with existing Hermes packages, never bootstrap the owner's install."""
import os
from pathlib import Path
import runpy
import sys

ROOT = Path('/Users/mac/.hermes/hermes-agent')
SCRATCH = Path('/Users/mac/.hermes/cache/scratch/hermes-line-s1')
assert Path(os.environ['HERMES_HOME']).resolve().is_relative_to(SCRATCH)
sys.dont_write_bytecode = True
sys.path.insert(0, str(ROOT))
# This path is the measured environment; no launcher or mutable owner state is used.
sys.path.insert(0, '/Users/mac/.hermes/installs/778930d1ee3bddda/environments/36f5841acd484742aba00d117c04db01/venv/lib/python3.14/site-packages')
script = sys.argv.pop(1)
if script == 'hermes':
    import hermes_bootstrap
    os.environ.pop('HERMES_DISABLE_LAZY_INSTALLS', None)
    from hermes_cli.main import main
    main()
else:
    sys.path.insert(0, str(Path(script).resolve().parent))
    runpy.run_path(script, run_name='__main__')
