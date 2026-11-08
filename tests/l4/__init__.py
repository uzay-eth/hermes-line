"""L4 tests (SMS / OTP). Offline, stdlib `unittest`; also collectable by pytest.

Run from the repo root:  tests/l4/run_tests.sh
"""
import os
import sys

_PLUGIN = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "plugin")
if _PLUGIN not in sys.path:
    sys.path.insert(0, _PLUGIN)
