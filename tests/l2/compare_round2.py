"""Offline before/after replay against the same expanded held-out manifest.

Reads the reviewed pre-fix detector from the local git object database only.
No worktree writes, network, external model or provider calls.
"""
import json
import subprocess
import sys
import types
import evaluate as scoring

BASELINE = 'c88f182'


def compare():
    source = subprocess.run(['git', 'show', BASELINE + ':plugin/line/detect/__init__.py'],
                            cwd=scoring.ROOT, check=True, capture_output=True, text=True).stdout
    baseline = types.ModuleType('_l2_round2_baseline')
    sys.modules[baseline.__name__] = baseline
    exec(compile(source, BASELINE + ':plugin/line/detect/__init__.py', 'exec'), baseline.__dict__)
    current = scoring.HoldTracker, scoring.Utterance
    try:
        scoring.HoldTracker = baseline.HoldTracker
        scoring.Utterance = baseline.Utterance
        before = [scoring.evaluate(split, mode) for split in ('train', 'held-out', 'soak') for mode in (False, True)]
    finally:
        scoring.HoldTracker, scoring.Utterance = current
        del sys.modules[baseline.__name__]
    after = [scoring.evaluate(split, mode) for split in ('train', 'held-out', 'soak') for mode in (False, True)]
    return {'baseline': BASELINE, 'before': before, 'after': after}


if __name__ == '__main__':
    print(json.dumps(compare(), indent=2))
