# BriefRooms Belief Core v2

## Status

**ENGINEERING-COMPLETE FOR SHADOW DATA COLLECTION.** Belief Core v2 is a decision-independent epistemic engine. It does not call BRACE, WES, BRACE-SPX, GPW Daily, position sizing, execution, or BUY/HOLD/SELL policy. It is intentionally unable to trade or auto-tune itself.

The next unknown is empirical, not architectural: calibration, source quality, stability and usefulness can only be measured after the engine receives real time-stamped evidence and later real outcomes.

## Architecture

```text
OBSERVATIONS
    -> EVIDENCE STORE
         source / source_ref / observed_at
         direction / strength / reliability
         evidence_type / independence_cluster / derived_from
    -> PROVENANCE + INDEPENDENCE AUDIT
         correlated-cluster de-duplication
         source_ref collision detection
         lineage-cycle detection
         within-cluster direction conflict detection
         future-evidence / look-ahead rejection
    -> BELIEF ENGINE
         probability
         evidence confidence (separate from probability)
         freshness / half-life decay
         support / opposition
         alternative hypotheses
    -> BELIEF AUDITOR
    -> WORLD STATE
    -> FROZEN FORECAST SNAPSHOT
         probability at forecast time
         evidence snapshot at forecast time
         target_at / horizon / regime / outcome_rule
    -> REAL-WORLD OUTCOME
    -> VERIFICATION
         Brier / log loss
         outcome provenance
    -> BELIEF CALIBRATION ENGINE
         reliability curve / ECE / MCE
         Brier decomposition
         over/under-confidence
         domain/entity/regime/horizon/belief slices
         source/evidence-type diagnostics
         source reliability review suggestions
         drift
         confidence-vs-error diagnostics
         competing-hypothesis multiclass metrics
         trajectory timing / flip diagnostics
    -> CALIBRATION MEMORY
         recommendations only; NO automatic weight/policy changes
```

## Core invariants

1. **Belief probability is not evidence confidence.** Probability estimates whether the claim is true; confidence describes how strong/fresh/diverse/independent the evidence basis is.
2. **Actions are outside the core.** No trade execution, sizing or policy API exists.
3. **No calibration from mutable current state.** A forecast must be frozen before the outcome. Verification uses the frozen probability and frozen evidence snapshot, not the later belief state.
4. **Legacy verification cannot contaminate calibration.** v1 records without a frozen forecast are migrated as `calibration_eligible=false`.
5. **No look-ahead evidence.** Evidence whose `observed_at` is after `as_of` is excluded and produces a critical audit finding.
6. **No double counting by publication count.** One `independence_cluster` contributes one representative statistical unit. All observations remain visible for provenance.
7. **No silent self-learning.** Calibration can suggest a reliability review but `automatic_tuning_enabled=false` is hard-coded.
8. **Competing hypotheses remain explicit.** `alternative_group` probabilities are normalized and can be resolved as one coherent forecast set.
9. **Audit trail is tamper-evident.** New ledger entries form a SHA-256 hash chain; legacy unchained entries are tolerated as a prefix and reported.

## Evidence contract

Required:
- `evidence_id`
- `belief_id`
- `source`
- `observed_at`
- `direction`: `+1` support, `-1` oppose
- `strength`: `[0,1]`
- `reliability`: `[0,1]`
- `independence_cluster`

Recommended:
- `source_type`: `primary | secondary | derived`
- `source_ref`: stable URI / filing ID / market-series reference / event ID
- `derived_from`: upstream evidence IDs when known
- `evidence_type`: e.g. `price`, `breadth`, `earnings`, `macro_release`, `filing`, `news`
- `note`, `metadata`

### Freshness

```text
freshness = 0.5 ** (age_hours / half_life_hours)
effective_mass = strength * reliability * freshness
```

## Belief definition contract

A belief specifies:
- `belief_id`
- atomic `claim`
- `prior_probability`
- `half_life_hours`
- `entity`
- `domain`
- optional `alternative_group`
- `tags`
- `horizon_hours`
- `outcome_rule`

`outcome_rule` should become deterministic when real adapters are connected. The compatibility default `manual_binary_resolution` is deliberately surfaced by the auditor as an informational finding.

## Probability update

The engine recomputes from the fixed prior and the currently valid representative evidence. It does **not** feed the previous posterior back as a new prior on every scheduler run.

```text
p = logistic(logit(prior) + 1.65 * signed_effective_mass)
```

This mapping is deliberately transparent. Its empirical calibration must be learned from shadow outcomes before any production use.

## Evidence confidence

Confidence is derived separately from:
- representative source reliability,
- freshness,
- independent-cluster coverage,
- source diversity,
- contradiction penalty,
- within-cluster interpretation-conflict penalty.

It is not treated as a probability of the claim being true.

## Provenance and audit controls

The auditor checks:
- future-dated evidence / look-ahead leakage (**critical**),
- known provenance cycles (**critical**),
- duplicate independence clusters,
- the same `source_ref` split across multiple independence clusters,
- conflicting directions inside one cluster,
- derived evidence without lineage,
- thin evidence,
- stale evidence,
- material support/opposition contradiction,
- missing primary source,
- weak provenance,
- source concentration,
- high probability with low evidence confidence,
- manual outcome rule.

## Frozen forecasts

`capture_forecast()` freezes:
- final probability (after alternative-group normalization),
- evidence confidence,
- `forecast_at`, `target_at`, horizon,
- domain/entity/regime,
- outcome rule,
- representative evidence IDs,
- full representative evidence snapshot and effective masses.

Forecast IDs are deterministic and immutable. Re-running the same shadow observation is idempotent; attempting to reuse the ID for different content fails.

`capture_all_forecasts()` freezes all current beliefs. Beliefs in the same `alternative_group` receive the same `forecast_set_id` for coherent later scoring.

## Verification

`verify_forecast()` records:
- frozen predicted probability,
- binary outcome,
- Brier score,
- log loss,
- resolution timestamp,
- outcome source/reference,
- frozen evidence snapshot,
- horizon/domain/entity/regime.

Verification before `target_at` is rejected unless explicitly marked `allow_early`; early records are excluded from calibration.

`verify_alternative_group()` resolves one frozen competing-hypothesis set with one winner and all other hypotheses false.

The compatibility `verify(belief_id, outcome)` method uses the newest due unresolved frozen forecast. If none exists, it creates a legacy diagnostic record that is **excluded from calibration**.

## Belief Calibration Engine

The engine measures:

### Proper scoring and reliability
- mean Brier score,
- mean log loss,
- reliability curve in 10 probability bins,
- Expected Calibration Error (ECE),
- Maximum Calibration Error (MCE),
- calibration bias (`mean predicted - observed rate`),
- Brier reliability/resolution/uncertainty decomposition,
- 50% threshold accuracy as a secondary diagnostic.

### Slices
- by domain,
- by entity,
- by regime,
- by horizon bucket,
- by belief ID,
- by outcome source.

### Competing hypotheses
For coherently resolved `alternative_group` forecast sets:
- multiclass Brier score,
- winner log loss,
- top-1 accuracy.

### Source and evidence-type memory
For representative evidence frozen before outcomes:
- independent evidence observation count,
- distinct forecast count,
- directional accuracy,
- effective-mass-weighted directional accuracy,
- mean reliability assigned at forecast time,
- mean Brier when present,
- bounded `suggested_reliability_delta` after sufficient data.

This is explicitly **associational attribution**, not causal proof that a source caused a correct/incorrect belief. Suggestions never change weights automatically.

### Confidence diagnostics
Evidence-confidence buckets are compared with later Brier error. This answers whether high evidence confidence is actually associated with lower forecast error without pretending confidence is itself a calibrated event probability.

### Drift
After a sufficient history, recent calibration is compared with the prior window using Brier and absolute calibration bias. Status can become `stable`, `improving` or `deteriorating`.

### Trajectory timing
For verified forecasts the core reports:
- belief-side flip count before target,
- first time the belief moved to the ultimately correct side,
- lead time before target.

## Sample-size governance

Diagnostics are available immediately but strong labels/recommendations are gated:
- calibration global gate: 30 eligible outcomes,
- dimension label: 8 outcomes,
- source/evidence reliability review: 15 distinct forecasts,
- drift: at least 30 outcomes split across two windows.

These are engineering safeguards, not claims of statistical significance. Production thresholds can be strengthened after observing real data volume and dependence structure.

## Persistence

Default: `data/belief_core/`
- `state.json`: definitions, evidence, beliefs, frozen forecasts, verification/calibration memory.
- `ledger.jsonl`: append-only/tamper-evident event history.
- `dashboard.json`: internal snapshot; intentionally ignored by git/public site.

State JSON writes are atomic. Evidence IDs, frozen forecast IDs and verification IDs are immutable/idempotent.

## Shadow runner

```bash
python scripts/belief_core_shadow.py --input evidence-batch.json --state-dir data/belief_core --regime normal
```

By default it recomputes beliefs and freezes forecasts. `--no-capture` is available for diagnostics only.

## Outcome verification runner

```bash
python scripts/belief_core_verify.py --input outcomes.json --state-dir data/belief_core
```

Example:

```json
{
  "verifications": [
    {
      "forecast_id": "forecast-...",
      "outcome": true,
      "verified_at": "2026-08-18T20:05:00Z",
      "outcome_source": "official_close",
      "outcome_ref": "market://SPX/close/2026-08-18"
    }
  ]
}
```

## Acceptance before decision-engine integration

Do **not** let BRACE/WES/BRACE-SPX consume Belief Core for decisions merely because v2 passes unit tests. First collect real shadow data and inspect:
- provenance quality and cluster collisions,
- evidence freshness,
- contradiction/cluster-conflict behavior,
- probability distribution and saturation,
- ECE/Brier/log loss and reliability curve,
- calibration by horizon/domain/regime,
- source/evidence-type diagnostics,
- belief flip-rate and lead time,
- pipeline reliability and unresolved outcome backlog,
- drift.

Only after stable shadow results should read-only engine adapters be introduced one at a time. Policy influence is a later gate.

## Tests

```bash
python -m unittest discover -s tests -p 'test_belief_core.py' -v
```

The v2 suite covers probability/confidence separation, decay, provenance de-duplication, future-evidence leakage, provenance cycles, source-ref cluster collisions, within-cluster interpretation conflict, alternatives, immutability/idempotency, frozen-forecast anti-leakage, early-verification rejection, v1 migration safety, Brier/log-loss calibration, source/evidence attribution, regime/horizon slices, multiclass competing-hypothesis scoring, reliability suggestions without auto-application, ledger tamper detection, unresolved forecasts and trajectory diagnostics.
