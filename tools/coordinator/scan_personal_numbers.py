#!/usr/bin/env python3
"""Scan tracked working-tree files. Output paths/counts only, never phone values."""
import argparse
import json
from pathlib import Path
import re
import subprocess
import sys
import unicodedata

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
from tools.trial.personal_numbers import load_personal_numbers, load_line_number


def patterns(values):
    # Match national/international forms, parentheses, spaces, dots and dashes.
    # Include embedded prefixes (e.g. postdial test fixtures) to avoid retaining
    # the number simply because more digits follow it.
    sep = r'[\s().\-\u2010-\u2015]*'
    return [re.compile(r'(?<!\d)(?:\+?1' + sep + r')?'
                       + sep.join(re.escape(d) for d in value[-10:]))
            for value in dict.fromkeys(values)]


def normalized(text):
    return ''.join(str(unicodedata.decimal(c)) if c.isdecimal() else c for c in text)


def masked(text, values):
    text = normalized(text)
    for value, pattern in zip(sorted(set(values)), patterns(sorted(set(values)))):
        text = pattern.sub('[NUMBER ending ' + value[-4:] + ']', text)
    return text


def scan(root, values):
    result = []
    checks = patterns(values)
    paths = subprocess.check_output(['git', 'ls-files', '-z'], cwd=root).decode().split('\0')
    for name in filter(None, paths):
        path = Path(root) / name
        if not path.is_file():
            continue
        content = normalized(path.read_bytes().decode('utf-8', errors='replace'))
        count = sum(len(p.findall(content)) for p in checks)
        if count:
            result.append({'path': name, 'count': count})
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=REPO)
    args = parser.parse_args()
    try:
        values = list(load_personal_numbers().values()) + [load_line_number()]
    except ValueError as exc:
        print(json.dumps({'error': str(exc)}))
        return 2
    hits = scan(args.root, values)
    for hit in hits:
        print(json.dumps(hit, sort_keys=True))
    print(json.dumps({'files': len(hits), 'matches': sum(h['count'] for h in hits)}))
    return 1 if hits else 0


if __name__ == '__main__':
    sys.exit(main())
