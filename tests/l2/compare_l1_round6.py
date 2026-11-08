"""Compare complete verbose unittest outcomes; no test or provider execution."""
import json
from pathlib import Path
import re

ROOT=Path(__file__).resolve().parent


def outcomes(path):
    current=None; found={}; occurrences={}
    for line in path.read_text().splitlines():
        start=re.match(r'^test_\S+ \(([^)]+)\)',line)
        if start:
            name=start[1]
            occurrences[name]=occurrences.get(name,0)+1
            current=name if occurrences[name]==1 else name+'#occurrence='+str(occurrences[name])
        result=re.search(r'(?:^|\.\.\. )(ok|FAIL|ERROR|skipped .*)$',line)
        if result and current:
            found[current]=result[1];current=None
    return found


if __name__=='__main__':
    before=outcomes(ROOT/'round6-l1-before.txt')
    after=outcomes(ROOT/'round6-l1-after.txt')
    changed=[dict(test=k,before=before.get(k),after=after.get(k)) for k in sorted(before.keys()|after.keys()) if before.get(k)!=after.get(k)]
    print(json.dumps(dict(before_tests=len(before),after_tests=len(after),changed=changed),indent=2))
