"""Generate the root README.md from plugin/README.md (the single source of user-facing text).

Usage, from the repository root:
    python3 -I docs/release/sync_readme.py            # write README.md
    python3 -I docs/release/sync_readme.py --check    # exit 1 if README.md is out of date

What differs in the root copy, and nothing else:
- the header comment (says the file is generated);
- image and link paths: `../tools/` in plugin/README.md becomes `tools/` at the root;
- the repo-level "Development and tests" section, appended at the end.
Standard library only; reads and writes only those two files.
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "plugin" / "README.md"
TARGET = ROOT / "README.md"

HEADER = (
    "<!-- GENERATED from plugin/README.md by docs/release/sync_readme.py. Do not edit here: change\n"
    "     plugin/README.md (the copy the plugin catalog renders), then run the script. -->"
)

EXTRA = """
## Development and tests

All suites run offline: no network, no Twilio, no phone.

```sh
PY=<the Python of your Hermes install>
PYTHONDONTWRITEBYTECODE=1 $PY -B -m unittest discover -s tests/l1     # also tests/l5, tests/l6
PYTHONDONTWRITEBYTECODE=1 $PY -B -m unittest discover -s tests/l4 -t .
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=plugin python3 -B -m unittest discover -s tests/l2
node --test tests/l3/pane.test.mjs tests/l3/browser.test.mjs tests/l3/live.test.mjs
node tools/pane-preview/preview.mjs shoot round4                   # pane screenshots, sample data
```

Latest counts: `docs/release/test-run.txt`. Live calls go only through the attended trial harness in `tools/trial/`
(see its README). Release texts: `docs/release/`; demo plan: `docs/demo/storyboard.md`.
"""


def render() -> str:
    text = SOURCE.read_text(encoding="utf-8")
    text, n = re.subn(r"<!--.*?-->", HEADER, text, count=1, flags=re.S)
    if n != 1:
        raise SystemExit("plugin/README.md has no header comment to replace")
    text = text.replace("](../tools/", "](tools/").replace("](../docs/", "](docs/")
    return text.rstrip("\n") + "\n" + EXTRA


def main() -> int:
    out = render()
    if "--check" in sys.argv[1:]:
        current = TARGET.read_text(encoding="utf-8") if TARGET.exists() else ""
        if current != out:
            print("README.md is out of date; run docs/release/sync_readme.py")
            return 1
        return 0
    TARGET.write_text(out, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
