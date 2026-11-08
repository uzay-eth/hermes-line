"""Desktop/dashboard backend for Hermes Line: routes under /api/plugins/hermes-line/.

Hermes imports this file as a standalone module, so it loads the plugin package by path
(under its own module name) and mounts ``line.server.desktop_api.create_router``. The REST
layer reaches the call service only through the profile's store, so it works whichever
process owns the line.
"""
import importlib.util
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_NAME = "hermes_line_backend"
if _NAME not in sys.modules:
    _spec = importlib.util.spec_from_file_location(_NAME, _ROOT / "__init__.py",
                                                   submodule_search_locations=[str(_ROOT)])
    _module = importlib.util.module_from_spec(_spec)
    sys.modules[_NAME] = _module
    _spec.loader.exec_module(_module)

from hermes_line_backend.line.server.desktop_api import create_router  # noqa: E402

router = create_router()
