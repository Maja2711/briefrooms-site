from __future__ import annotations
import json, math, os, urllib.parse, urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from belief_adapter_contract import AdapterResult, EvidenceAssessment, Observation, clamp, observation_to_evidence
from belief_core import BeliefDefinition, iso_z

MACRO_BELIEFS = (
    BeliefDefinition("eurusd.macro_surprise.supportive","Relative US versus euro-area macro impulse is supportive for EUR/USD",prior_probability=.50,half_life_hours=12,entity="EURUSD",domain="macro_surprise",tags=("shared","MACRO","EURUSD","macro"),horizon_hours=24,outcome_rule="eurusd_close_above_reference"),
    BeliefDefinition("eurusd.policy_differential.supportive","Relative ECB versus Fed policy pressure is supportive for EUR/USD",prior_probability=.50,half_life_hours=18,entity="EURUSD",domain="policy_differential",tags=("shared","MACRO","EURUSD","macro"),horizon_hours=24,outcome_rule="eurusd_close_above_reference"),
)

EUROSTAT_API = "https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data"
BEA_API = "https://apps.bea.gov/api/data/"
CONSENSUS_PATH_ENV = "BELIEF_MACRO_CONSENSUS_PATH"

@dataclass(frozen=True)
class MacroRelease:
    region: str
    indicator: str
    period: str
    actual: float
    previous: Optional[float]
    consensus: Optional[float]
    revision: Optional[float]
    unit: str
    source: str
    source_ref: str
    observed_at: datetime

    @property
    def surprise(self) -> Optional[float]:
        if self.consensus is None:
            return None
        return self.actual - self.consensus

def load_consensus() -> Dict[str, Any]:
    path=os.environ.get(CONSENSUS_PATH_ENV,"").strip()
    if not path: return {}
    try:
        payload=json.loads(open(path,encoding="utf-8").read())
    except (OSError,json.JSONDecodeError):
        return {}
    provider=str(payload.get("provider") or "").strip()
    if not provider:
        return {}
    return payload

def consensus_for(payload: Mapping[str,Any], region: str, indicator: str, period: str) -> Optional[float]:
    rows=payload.get("releases") if isinstance(payload.get("releases"),list) else []
    for row in rows:
        if not isinstance(row,Mapping): continue
        if str(row.get("region"))==region and str(row.get("indicator"))==indicator and str(row.get("period"))==period:
            try: return float(row["consensus"])
            except (KeyError,TypeError,ValueError): return None
    return None

def release_observation(row: MacroRelease) -> Observation:
    cluster=f"macro_release:{row.region}:{row.indicator}:{row.period}"
    return Observation.make(
        adapter="macro_release",
        metric="macro_release_actual",
        entity="US_MACRO" if row.region=="US" else "EU_MACRO",
        observed_at=iso_z(row.observed_at),
        value={"actual":row.actual,"consensus":row.consensus,"previous":row.previous,"revision":row.revision,"surprise":row.surprise},
        unit=row.unit, source=row.source, source_type="primary", source_ref=row.source_ref,
        reliability=.99, independence_cluster=cluster, tags=("macro_release",row.region,row.indicator),
        metadata={"region":row.region,"indicator":row.indicator,"period":row.period,"actual":row.actual,
                  "consensus":row.consensus,"previous":row.previous,"revision":row.revision,
                  "surprise":row.surprise,"consensus_available":row.consensus is not None},
    )

def _signal(row: MacroRelease) -> float:
    """Economic impulse, not a trade rule. Positive means relatively EUR-supportive."""
    surprise=row.surprise
    baseline=surprise if surprise is not None else (row.actual-row.previous if row.previous is not None else 0.0)
    scale=max(abs(row.consensus or 0.0),abs(row.previous or 0.0),abs(row.actual),1.0)
    z=clamp(baseline/scale*8.0,-1.0,1.0)
    name=row.indicator.lower()
    inflation=any(x in name for x in ("cpi","pce","hicp","inflation"))
    labor=any(x in name for x in ("payroll","employment","unemployment"))
    growth=any(x in name for x in ("gdp","retail","pmi","industrial"))
    if labor and "unemployment" in name: z=-z
    # Stronger US inflation/growth/labor normally raises relative USD pressure;
    # stronger euro-area data normally raises relative EUR pressure.
    if inflation or labor or growth:
        return z if row.region=="EU" else -z
    return 0.0

def release_evidence(row: MacroRelease):
    score=_signal(row)
    if abs(score)<.03: return ()
    primary=release_observation(row)
    derived=Observation.make(
        adapter="macro_release", metric="eurusd_relative_macro_impulse", entity="EURUSD",
        observed_at=iso_z(row.observed_at), value=round(score,6), unit="normalized_impulse",
        source=f"Deterministic transform of {row.source}", source_type="derived",
        source_ref=f"derived:{primary.observation_id}", reliability=.94,
        independence_cluster=primary.independence_cluster, tags=("EURUSD","macro","deterministic"),
        metadata={"upstream_observation_id":primary.observation_id,"primary_source_ref":row.source_ref,
                  "region":row.region,"indicator":row.indicator,"period":row.period,
                  "consensus_available":row.consensus is not None,"surprise":row.surprise},
    )
    ev=observation_to_evidence(derived,EvidenceAssessment(
        belief_id="eurusd.macro_surprise.supportive",direction=1 if score>0 else -1,
        strength=clamp(abs(score)*.55,.08,.55),evidence_type="official_macro_release",
        note=f"{row.region} {row.indicator} {row.period}: normalized relative EUR/USD macro impulse {score:+.3f}",
        independence_cluster=primary.independence_cluster,
        metadata={"primary_observation_id":primary.observation_id,"primary_source_ref":row.source_ref,
                  "actual":row.actual,"consensus":row.consensus,"previous":row.previous,
                  "revision":row.revision,"surprise":row.surprise},
    ))
    return primary,derived,ev

class MacroReleaseAdapter:
    """Normalizes official releases supplied by source collectors.

    Network collectors may append MacroRelease rows; this contract deliberately
    refuses to invent consensus. A consensus is accepted only from an explicitly
    named provider payload in BELIEF_MACRO_CONSENSUS_PATH.
    """
    name="macro_release"
    version="1.0.0"
    def __init__(self, releases: Sequence[MacroRelease]=()) -> None:
        self.releases=tuple(releases)
    def run(self, now: datetime) -> AdapterResult:
        observations=[]; evidence=[]
        for row in self.releases:
            result=release_evidence(row)
            if not result:
                observations.append(release_observation(row)); continue
            p,d,e=result; observations.extend((p,d)); evidence.append(e)
        return AdapterResult(self.name,tuple(observations),tuple(evidence))
