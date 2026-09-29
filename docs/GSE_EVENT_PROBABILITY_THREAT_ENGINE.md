# GSE Event Probability / Threat Engine

## Purpose

The Event Probability / Threat Engine is a shadow-only layer inside `IN-03` GSE.
It answers a different question from the existing GSE market-transmission model:

- existing GSE: **what is the probable market reaction given geopolitical conditions/scenarios?**
- Event Threat Engine: **what is the prospective probability that a precisely defined geopolitical event will be observed by a fixed horizon?**

The two probabilities must never be conflated.

## Position in the GSE architecture

```text
Geopolitical Evidence
        |
        v
Event Probability / Threat Engine
precursors -> P(event) -> frozen event forecast
        |                    |
        |                    v
        |              verification
        |                    |
        |                    v
        |             Brier / log loss
        |
        +--------------------------+
                                   v
Existing GSE scenario / transmission layer
scenario -> market impact -> frozen asset forecast -> verification
                                   |
                                   v
                              GSE v2 learning
```

The Event Threat Engine does not replace the existing GSE v1/v2 market-reaction path.
It is an additional, upstream research layer.

## Initial event set

The first prospective contract covers explicitly defined armed-event outcomes:

- `russia_nato_attack_any_eastern_flank`
- `russia_attack_poland`
- `russia_attack_lithuania`
- `russia_attack_latvia`
- `russia_attack_estonia`

The aggregate event is a union-style eastern-flank event. Country-specific events are
kept separate so they can later be calibrated independently.

The initial horizons are:

- 168 h / 7 days,
- 720 h / 30 days,
- 2160 h / 90 days.

## Precursor model

The engine consumes the existing immutable `gse_evidence.jsonl` ledger and extracts
auditable precursor categories:

- force posture,
- mobilization / logistics,
- border / airspace incidents,
- hybrid / cyber activity,
- coercive rhetoric,
- alliance response.

Evidence must match the Russia actor family and the relevant target/region before it
can contribute. Country evidence is isolated: Lithuania-specific evidence must not
raise a Latvia forecast merely because both are in the same region.

The evidence score uses source reliability, time decay, independent-source diversity,
primary-source presence and precursor-category breadth. No LLM is used to silently
rewrite the score.

## Probability semantics

The first probability mapping is deliberately labelled:

`research_event_probability_uncalibrated`

Its numerical prior is an **engineering seed**, not a historical attack base rate and
not an authoritative intelligence estimate. This is intentional: BriefRooms does not
currently possess a sufficiently clean point-in-time historical dataset for empirical
country-level attack priors.

Every frozen forecast stores both:

- `prior_probability`,
- `predicted_probability`.

This enables direct prospective measurement of whether the precursor layer improves
on its own prior instead of rewarding the model merely for assigning low probability
to rare events.

## Freeze and verification

Current estimates are refreshed after every hourly GSE evidence scan.

Prospective forecasts are frozen once per day at 00 UTC. Manual workflow dispatch can
force an additional freeze. Frozen forecasts are append-only and are never rewritten.

A positive outcome requires qualifying realization evidence before the target time.
Hypothetical language such as "could attack", "might invade", simulations and scenarios
does not count as realization.

A positive event is confirmed by either:

- at least one qualifying primary-source item, or
- at least two independent qualifying secondary sources.

For a negative outcome, data-coverage sufficiency is required before the row becomes
eligible for calibration. This prevents a source outage from being silently scored as
"no attack".

The operational outcome is therefore strictly:

> qualifying report of the defined event observed by the target timestamp.

It is not a claim that publication timestamp equals the exact military event timestamp.

## Scoring

Every eligible verification records:

- binary outcome,
- Brier score,
- prior Brier score,
- delta Brier versus stored prior,
- log loss,
- coverage cycles and days,
- confirmation-source count.

Calibration is reported:

- overall,
- by event type,
- by horizon.

For rare events, the report also exposes positive-count scarcity. A large sample with
zero positives is not treated as evidence that discrimination quality has been proven.

## Runtime state

The private cumulative `gse-shadow-state-v2` artifact gains:

```text
gse_event_threat_state.json
gse_event_threat_cycles.jsonl
gse_event_probability_forecasts.jsonl
gse_event_probability_verifications.jsonl
gse_event_probability_calibration.json
```

The public GSE Lab receives only a sanitized projection: target, horizon, prior,
P(event), confidence, precursor category names, aggregate evidence/source counts and
calibration metrics. Raw Evidence IDs, realization rows and private forecast IDs are
not exposed.

## Safety / authority boundary

The Event Threat Engine is `shadow` and `research_only`.

Hard-disabled capabilities:

- trade execution,
- policy output,
- Belief Core writeback,
- decision-engine authority,
- automatic tuning,
- automatic promotion,
- frozen-forecast rewrite.

A later calibrated version may be reviewed by humans, but no prospective result can
promote itself or acquire execution authority.
