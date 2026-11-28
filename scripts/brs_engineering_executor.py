"""Allowlisted pilot: repair agent sandbox settings in a fresh candidate directory.

No shell, GitHub mutation, model calls, production authority or release verdict.
"""
import argparse
import hashlib
import json
import re
import tomllib
from pathlib import Path
from brs_engineering_graph import Job, execute

ROLES = ('search', 'grep', 'audit', 'architect', 'builder', 'tester')


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def expected(role):
    return 'workspace-write' if role in ('builder', 'tester') else 'read-only'


def read_profile(root, role):
    path = root / '.codex' / 'agents' / (role + '.toml')
    if any(p.is_symlink() for p in (root / '.codex', path.parent, path)):
        raise ValueError('symlinks not allowed')
    with path.open('rb') as f:
        raw = f.read(65537)
    if len(raw) > 65536:
        raise ValueError('profile too large')
    return raw


def candidate(raw, role):
    text = raw.decode('utf-8')
    before = tomllib.loads(text)
    if before.get('name') != role or not isinstance(before.get('developer_instructions'), str) or not before['developer_instructions'].strip():
        raise ValueError('invalid profile identity or instructions')
    if before.get('sandbox_mode') == expected(role):
        return raw
    # Only replace one ordinary top-level assignment. Verify semantic equality.
    pattern = r'(?m)^sandbox_mode[ \t]*=[ \t]*["\x27][a-z-]+["\x27][ \t]*$'
    if len(re.findall(pattern, text)) != 1:
        raise ValueError('unsupported sandbox assignment')
    new = re.sub(pattern, 'sandbox_mode = "' + expected(role) + '"', text)
    if tomllib.loads(new) != dict(before, sandbox_mode=expected(role)):
        raise ValueError('unintended semantic change')
    return new.encode('utf-8')


def run(source, output):
    source = Path(source).resolve(strict=True)
    output = Path(output).resolve()
    if output == source or source in output.parents:
        raise ValueError('output must be outside source')
    originals, proposals = {}, {}
    def detect(_):
        originals.update({r: read_profile(source, r) for r in ROLES})
        return {'status': 'PASS', 'hashes': {r: digest(v) for r, v in originals.items()}}
    def diagnose(_):
        proposals.update({r: candidate(originals[r], r) for r in ROLES})
        return {'status': 'PASS', 'changes': [r for r in ROLES if proposals[r] != originals[r]]}
    def repair(_):
        if any(read_profile(source, r) != originals[r] for r in ROLES):
            raise ValueError('source changed')
        output.mkdir(mode=0o700, exist_ok=False)
        target = output / '.codex' / 'agents'
        target.mkdir(parents=True)
        for role in ROLES:
            with (target / (role + '.toml')).open('xb') as f:
                f.write(proposals[role])
        return {'status': 'PASS', 'candidate_hashes': {r: digest(v) for r, v in proposals.items()}}
    def verify(_):
        for r in ROLES:
            actual = read_profile(output, r)
            if actual != proposals[r] or candidate(actual, r) != actual or read_profile(source, r) != originals[r]:
                return {'status': 'FAIL', 'reason': 'candidate/source mismatch'}
        return {'status': 'PASS', 'scope': 'sandbox settings only'}
    def learn(deps):
        return {'status': 'PASS', 'lesson': 'candidate verified locally; independent review and commit-bound CI still required'}
    adapters = dict(detect=detect, diagnose=diagnose, repair=repair, verify=verify, learn=learn)
    names = tuple(adapters)
    jobs = [Job(n, n, (names[i-1],) if i else ()) for i, n in enumerate(names)]
    results = execute(jobs, adapters)
    return {'schema': 'brs-engineering-pilot-v1', 'results': results,
            'candidate_valid': results['learn']['status'] == 'PASS', 'release_authority': False}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source', required=True)
    p.add_argument('--output', required=True)
    args = p.parse_args()
    report = run(args.source, args.output)
    print(json.dumps(report, indent=2))
    return int(not report['candidate_valid'])


if __name__ == '__main__':
    raise SystemExit(main())
