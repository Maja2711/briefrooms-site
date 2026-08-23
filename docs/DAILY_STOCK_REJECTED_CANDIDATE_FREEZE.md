# PR29.1 — Daily Stock Rejected Candidate Freeze

PR29.1 adds a prospective, immutable decision-path freeze for rejected GPW and US Daily Stock LONG candidates.

## Purpose

The freeze captures the state that existed at decision time so PR29 can later answer why a LONG candidate was rejected, whether the first blocking gate was useful, and whether a final FLAT was a correct abstention or a missed opportunity.

The freeze is diagnostic only. It does not change ranking, admission, thresholds, sizing, execution or any source-engine decision.

## Point-in-time contract

For every rejected candidate that can be reconstructed from information available during the same publisher cycle, the freeze records:

- candidate identity and market,
- reference price and entry zone,
- stop, target, reward/risk and risk percent,
- quantitative scores that existed before rejection,
- ordered gate path,
- first blocking gate,
- explicit engine rejection reason,
- source decision timestamp and source payload hash,
- immutable candidate and freeze SHA-256 hashes.

Provider failures or candidates that never reached a legitimate economic candidate state remain non-settleable. PR29.1 never invents a LONG risk plan after an outcome is known.

## Immutability

A valid freeze for the same source decision timestamp is preserved on later publisher runs. If the source payload changes after a freeze already exists for that decision, the freeze is not silently rewritten.

## Integration

The GPW and US publisher workflows run the freeze immediately after their final output is generated and validated, before the output is committed to `main`.

PR29 reads the embedded freeze through `counterfactual_decision_gate_diagnostics_v29_1.py` and upgrades eligible rejected candidates from `insufficient_counterfactual_state` to a real `risk_plan` counterfactual.

## Anti-hindsight and authority

- historical backfill: false
- news/LLM rerun: false
- counterfactual direction synthesis: false
- ranking writeback: false
- gate writeback: false
- source-engine writeback: false
- trade execution: false

A rejected candidate can be economically settled only when its LONG plan and gate path were frozen before the later market outcome.
