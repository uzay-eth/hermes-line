"""STEER-19 (Nous re-review of 115faa3d): the call-profile check reads config.yaml with Hermes's bundled
``hermes_yaml.safe_load``, not PyYAML, which neither Hermes core nor plugin.yaml provides. On an install without
PyYAML, ``problems()`` on a fresh neutral profile is [] (115faa3d: ``["config.yaml unreadable"]``, so every outbound
call and inbound caller was refused).

The same check runs twice: in a child interpreter in which ``import yaml`` fails whatever is installed, and in this
interpreter when PyYAML is absent from it (else that test is skipped and says so). A third test keeps every
third-party import in plugin/ declared in plugin.yaml or shipped by Hermes core.
Offline, SYNTHETIC profiles in temp dirs; no real profile is read.
"""
import ast
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import harness  # noqa: F401  (loads hermes_yaml from the Hermes checkout, as Hermes's bootstrap provides it)

from plugin.line.engine import call_profile, hermes_glue

from test_s7_call_profile import CLONE_PROBLEMS, STOCK_SOUL, make_profile

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]

_CHILD = r"""
import importlib.abc, json, sys

class NoPyYAML(importlib.abc.MetaPathFinder):
    def find_spec(self, name, path=None, target=None):
        if name in ("yaml", "_yaml") or name.startswith("yaml."):
            raise ModuleNotFoundError(f"No module named {name!r} (PyYAML blocked for this test)", name=name)
        return None

sys.meta_path.insert(0, NoPyYAML())
sys.path.insert(0, sys.argv[1])
from pathlib import Path
from unittest import mock
import harness
from plugin.line.engine import call_profile, hermes_glue
from test_s7_call_profile import STOCK_SOUL, make_profile
try:
    import yaml
    absent = False
except ModuleNotFoundError:
    absent = True
neutral = make_profile(Path(sys.argv[2]), cloned=False)
direct = call_profile.problems(neutral, [STOCK_SOUL])
with mock.patch.object(hermes_glue, "_profile_dir", return_value=neutral), \
        mock.patch.object(hermes_glue, "_neutral_souls", return_value=[STOCK_SOUL]):
    glue = hermes_glue.call_profile_problems("hermes-line-calls")
cloned = call_profile.problems(make_profile(Path(sys.argv[3]), cloned=True), [STOCK_SOUL])
print(json.dumps({"pyyaml_absent": absent, "pyyaml_loaded": "yaml" in sys.modules, "direct": direct, "glue": glue,
                  "cloned": cloned}))
"""


def _check_here() -> dict:
    neutral = make_profile(Path(tempfile.mkdtemp(prefix="hl-s19-")), cloned=False)
    with mock.patch.object(hermes_glue, "_profile_dir", create=True, return_value=neutral), \
            mock.patch.object(hermes_glue, "_neutral_souls", create=True, return_value=[STOCK_SOUL]):
        glue = hermes_glue.call_profile_problems("hermes-line-calls")
    return {"direct": call_profile.problems(neutral, [STOCK_SOUL]), "glue": glue,
            "cloned": call_profile.problems(make_profile(Path(tempfile.mkdtemp(prefix="hl-s19-")), cloned=True),
                                            [STOCK_SOUL])}


class NoPyYAMLTests(unittest.TestCase):

    def assert_neutral_passes(self, result: dict) -> None:
        self.assertEqual(result["direct"], [])  # 115faa3d: ['config.yaml unreadable']
        self.assertEqual(result["glue"], [])
        self.assertEqual(result["cloned"], CLONE_PROBLEMS)  # the config is really parsed, not skipped

    def test_S19_a_neutral_profile_passes_where_import_yaml_fails(self):
        out = subprocess.run([sys.executable, "-B", "-c", _CHILD, str(HERE), tempfile.mkdtemp(prefix="hl-s19-"),
                              tempfile.mkdtemp(prefix="hl-s19-")],
                             cwd=HERE, capture_output=True, text=True, timeout=120, env=dict(os.environ))
        self.assertEqual(out.returncode, 0, out.stderr[-2000:])
        result = json.loads(out.stdout.strip().splitlines()[-1])
        self.assertTrue(result["pyyaml_absent"], result)
        self.assertFalse(result["pyyaml_loaded"], result)
        self.assert_neutral_passes(result)

    def test_S19_a_neutral_profile_passes_in_this_interpreter_without_pyyaml(self):
        spec = importlib.util.find_spec("yaml")
        if spec is not None:
            self.skipTest(f"PyYAML is installed in this interpreter ({spec.origin}); the child-interpreter test "
                          "blocks it instead")
        self.assert_neutral_passes(_check_here())
        self.assertNotIn("yaml", sys.modules)

    def test_S19_every_third_party_import_in_the_plugin_is_declared_or_ships_with_hermes(self):
        """Grep-style over every plugin/**/*.py, no exemption: each top-level import that is not the standard
        library, the plugin itself or Hermes is in plugin.yaml python_dependencies. ``fastapi`` is a Hermes core
        dependency (pyproject ``dependencies``), imported only by the dashboard backend that Hermes's FastAPI app
        mounts."""
        from hermes_yaml import safe_load
        manifest = safe_load((ROOT / "plugin" / "plugin.yaml").read_text(encoding="utf-8"))
        declared = {dep.split(";")[0].split("[")[0].strip().split("<")[0].split(">")[0].split("=")[0].split("!")[0]
                    .split("~")[0].strip().lower().replace("-", "_") for dep in manifest["python_dependencies"]}
        hermes = {"agent", "gateway", "hermes_cli", "hermes_yaml", "plugins"}  # Hermes root packages/modules
        hermes_core_deps = {"fastapi"}
        own = {"line", "plugin", "hermes_line_backend"}
        found = {}
        sources = [p for p in sorted((ROOT / "plugin").rglob("*.py")) if "__pycache__" not in p.parts]
        self.assertGreater(len(sources), 20)
        for path in sources:
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                names = [a.name for a in node.names] if isinstance(node, ast.Import) else \
                    [node.module] if isinstance(node, ast.ImportFrom) and not node.level and node.module else []
                for name in names:
                    top = name.split(".")[0]
                    if top not in sys.stdlib_module_names and top != "__future__":
                        found.setdefault(top, f"{path.relative_to(ROOT)}:{node.lineno}")
        undeclared = {top: where for top, where in found.items()
                      if top not in declared | hermes | hermes_core_deps | own}
        self.assertEqual(undeclared, {})  # 115faa3d: {'yaml': 'plugin/line/engine/call_profile.py:108'}
        self.assertNotIn("yaml", found)


if __name__ == "__main__":
    unittest.main()
