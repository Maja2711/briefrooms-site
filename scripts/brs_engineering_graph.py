"""Deterministic DAG runner for trusted engineering adapters; sequential fallback."""
from dataclasses import dataclass


@dataclass(frozen=True)
class Job:
    id: str
    action: str
    deps: tuple = ()
    priority: int = 0


def execute(jobs, adapters):
    jobs = tuple(jobs)
    index = {j.id: j for j in jobs}
    if len(index) != len(jobs) or any(not j.id or j.action not in adapters for j in jobs):
        raise ValueError('duplicate ID or unknown action')
    if any(set(j.deps) - index.keys() for j in jobs):
        raise ValueError('unknown dependency')
    # Validate the entire DAG before invoking any adapter.
    order, pending = [], set(index)
    while pending:
        ready = sorted((index[k] for k in pending if set(index[k].deps) <= set(order)),
                       key=lambda j: (-j.priority, j.id))
        if not ready:
            raise ValueError('dependency cycle')
        for j in ready:
            order.append(j.id)
            pending.remove(j.id)
    results = {}
    for key in order:
        j = index[key]
        deps = {k: results[k] for k in j.deps}
        if any(v['status'] != 'PASS' for v in deps.values()):
            results[key] = {'status': 'BLOCKED', 'reason': 'upstream failed'}
            continue
        try:
            value = adapters[j.action](deps)
            if not isinstance(value, dict) or value.get('status') not in ('PASS', 'FAIL'):
                raise ValueError('invalid adapter result')
            results[key] = value
        except Exception as exc:
            # Do not emit exception text (may contain secrets).
            results[key] = {'status': 'FAIL', 'reason': type(exc).__name__}
    return results
