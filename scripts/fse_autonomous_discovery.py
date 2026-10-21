#!/usr/bin/env python3
"""FSE Autonomous Structure Discovery (ASD-1), strictly research/shadow.

Unsupervised, price-normalised temporal motifs; NO chart-pattern templates,
NO future bars in an active forecast, NO execution or model promotion.
Historical clustering discovers candidate families. Only newly frozen hourly
snapshots and *subsequent* four market bars are prospective evidence.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import statistics
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

try:
    import fractal_structure_engine as market
except ImportError:
    from scripts import fractal_structure_engine as market

VERSION = "FSE-ASD-2.0"
LEGACY_VERSION = "FSE-ASD-1.0"
HORIZON = 4
WINDOWS = (32, 64)
MIN_SUPPORT = 6
MAX_NEW_FAMILIES_PER_INSTRUMENT_CYCLE = 4  # rate limit only, no lifetime discovery cap
MAX_CANDIDATES = 420
REQUIRED_FORWARD_N = 40
MAX_SELECTION_TRIALS = 24
SELECTION_FAMILY_ALPHA = 0.05 / (MAX_SELECTION_TRIALS * 2)
MATCH_MAX_RADIUS_FRACTION = 0.85
MATCH_MIN_MARGIN = 0.10
MAX_REFERENCE_AGE = timedelta(minutes=5)
TARGET_SELECTION = "FIRST_4_MARKET_BAR_OPENS_AT_OR_AFTER_FREEZE"
STATE_NAME = "autonomous_discovery_state.json"
PUBLIC_PATH = Path("data/investments/fse_discovery_public.json")
ZERO_AUTHORITY = {"execution": False, "trading_decisions": False,
                  "risk_policy_writeback": False, "belief_core_writeback": False,
                  "automatic_promotion": False, "historical_backfill": False}


def canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def utc(value: str | datetime) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00")) if isinstance(value, str) else value
    if parsed.tzinfo is None:
        raise ValueError("Timestamp must be timezone-aware")
    return parsed.astimezone(timezone.utc)


def mean(vals: Sequence[float]) -> float:
    return statistics.fmean(vals) if vals else 0.0


def clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def quantiles_path(closes: Sequence[float], points: int = 12) -> tuple[float, ...]:
    if len(closes) < 16 or min(closes) <= 0:
        return ()
    logs = [math.log(p / closes[0]) for p in closes]
    diff = [b - a for a, b in zip(logs, logs[1:])]
    path_scale = math.sqrt(mean([v * v for v in logs]))
    if path_scale < 1e-10:
        return ()
    norm = [clamp(x / path_scale, -4, 4) for x in logs]
    result = []
    for k in range(points):
        pos = (len(norm) - 1) * k / (points - 1)
        a = int(math.floor(pos)); b = int(math.ceil(pos))
        result.append(norm[a] if a == b else norm[a] + (norm[b] - norm[a]) * (pos - a))
    return tuple(result)


def fingerprint(closes: Sequence[float]) -> tuple[float, ...]:
    shape = quantiles_path(closes)
    if not shape:
        return ()
    rets = [math.log(b / a) for a, b in zip(closes, closes[1:])]
    mid = len(rets) // 2
    rv = math.sqrt(mean([r * r for r in rets])) or 1e-12
    first = math.sqrt(mean([r * r for r in rets[:mid]]))
    last = math.sqrt(mean([r * r for r in rets[mid:]]))
    turns = sum((a > 0) != (b > 0) for a, b in zip(rets, rets[1:]) if a != 0 and b != 0)
    efficiency = sum(rets) / max(sum(abs(r) for r in rets), 1e-12)
    return tuple(round(x, 5) for x in (
        *shape, 0.6 * efficiency, 0.3 * (2 * turns / max(len(rets) - 1, 1) - 1),
        0.3 * clamp(math.log(max(last, 1e-12) / max(first, 1e-12)), -2, 2),
    ))


def distance(a: Sequence[float], b: Sequence[float]) -> float:
    if len(a) != len(b) or not a:
        return math.inf
    return math.sqrt(mean([(x - y) ** 2 for x, y in zip(a, b)]))


def candidates(bars: Sequence[Any], window: int, query_exclusion: bool = True) -> list[dict[str, Any]]:
    """Training windows must have HORIZON *historical* forward bars available.

    Future outcomes are used only for exploratory conditional statistics,
    never for unsupervised grouping or prospective credit.
    """
    query_start = len(bars) - window
    end_max = (query_start if query_exclusion else len(bars)) - 1 - HORIZON
    if end_max < window + MIN_SUPPORT * 8:
        return []
    start = max(window - 1, end_max - MAX_CANDIDATES * 8)
    rows = []
    for end in range(start, end_max + 1, 8):
        segment = [float(x.close) for x in bars[end - window + 1:end + 1]]
        vector = fingerprint(segment)
        if not vector:
            continue
        base, future = float(bars[end].close), float(bars[end + HORIZON].close)
        if base <= 0 or future <= 0:
            continue
        rows.append({"vector": vector, "start_at": bars[end-window+1].timestamp.isoformat(),
                     "end_at": bars[end].timestamp.isoformat(), "target_end_at": bars[end+HORIZON].timestamp.isoformat(),
                     "start_index": end-window+1, "end_index": end, "target_end_index": end+HORIZON,
                     "return": future / base - 1, "window": window})
    return rows


def independent_episodes(rows: Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    """Maximum non-overlapping historical motif+target intervals, not raw windows."""
    valid=[r for r in rows if isinstance(r.get("start_index"),int) and isinstance(r.get("target_end_index"),int)]
    ordered=sorted(valid,key=lambda r:(r["target_end_index"],r["start_index"],str(r.get("end_at")),digest(r["vector"])))
    selected=[]; previous_end=-1
    for row in ordered:
        if row["start_index"]>previous_end:
            selected.append(row); previous_end=row["target_end_index"]
    return selected


def learn_groups(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Density-connected clusters, learned from data; no predefined structures.

    Adaptive nearest-neighbor threshold, medoid refinement (prevents chaining
    one large uninformative family). Outcomes are ignored by this routine.
    """
    n = len(rows)
    if n < MIN_SUPPORT:
        return []
    vectors = [r["vector"] for r in rows]
    dists = [[0.0] * n for _ in range(n)]
    for i in range(n):
        for j in range(i + 1, n):
            dists[i][j] = dists[j][i] = distance(vectors[i], vectors[j])
    kth = min(5, n - 1)
    radii = [sorted(dists[i][j] for j in range(n) if j != i)[kth - 1] for i in range(n)]
    eps = clamp(statistics.median(radii) * 0.90, 0.10, 0.42)
    parent = list(range(n))
    core = {i for i in range(n) if sum(d<=eps for d in dists[i])>=MIN_SUPPORT}

    def root(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for i in range(n):
        for j in range(i + 1, n):
            if i in core and j in core and dists[i][j] <= eps:
                ri, rj = root(i), root(j)
                if ri != rj:
                    parent[max(ri, rj)] = min(ri, rj)
    components: dict[int, list[int]] = {}
    for i in sorted(core):
        components.setdefault(root(i), []).append(i)
    # Border points may join one dense component; they never connect components.
    for i in range(n):
        if i in core:continue
        eligible=[j for j in core if dists[i][j]<=eps]
        if eligible:
            nearest=min(eligible,key=lambda j:(dists[i][j],digest(vectors[j]),str(rows[j].get("end_at"))))
            components[root(nearest)].append(i)
    clusters = []
    for component in components.values():
        if len(component) < MIN_SUPPORT:
            continue
        center = min(component, key=lambda i: (mean([dists[i][j] for j in component]),digest(vectors[i]),str(rows[i].get("end_at"))))
        retained = [i for i in component if dists[center][i] <= eps]
        members=[rows[i] for i in retained]
        independent=independent_episodes(members)
        if len(independent) < MIN_SUPPORT:
            continue
        # Never include outcome values in finding the cluster.
        clusters.append({"centroid": list(vectors[center]), "radius": eps,
                         "members": members, "independent_members": independent,
                         "independent_count":len(independent),
                         "compactness": statistics.median(dists[center][i]/max(eps,1e-12) for i in retained),
                         "count": len(retained)})
    return sorted(clusters, key=lambda x: (-x["independent_count"],-x["count"], digest(x["centroid"])))


def describe(centroid: Sequence[float], sample: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Machine-generated name and restrained applications, not pattern templates."""
    path = centroid[:12]
    magnitude = path[-1] - path[0]
    changes = [b - a for a, b in zip(path, path[1:])]
    turns = sum((a > 0) != (b > 0) for a, b in zip(changes, changes[1:]) if a != 0 and b != 0)
    ratio = abs(magnitude) / max(sum(abs(v) for v in changes), 1e-12)
    direction = "wznoszący" if magnitude > 0.2 else "opadający" if magnitude < -0.2 else "boczny"
    rhythm = "wielopulsowy" if turns >= 5 else "falowy" if turns >= 3 else "ciągły"
    character = "zwarty" if ratio >= 0.65 else "poszarpany" if ratio < 0.32 else "zmienny"
    # Labels describe discovered medoids; they do not define eligible patterns.
    name = f"{rhythm.capitalize()} ślad {direction} · {character}"
    up = sum(r["return"] > 0 for r in sample)
    count = len(sample)
    historical_p = (up + 2) / (count + 4)
    returns = [r["return"] for r in sample]
    forward_abs = statistics.median(abs(r) for r in returns)
    direction_hypothesis = ("większą częstość dodatnich zmian" if historical_p > 0.56 else
                            "większą częstość ujemnych zmian" if historical_p < 0.44 else
                            "brak wyraźnej przewagi kierunku")
    conclusion = (f"Rodzina powtarza {rhythm} przebieg {direction} o {character} charakterze. "
                  f"W {count} historycznych analogiach zaobserwowano {direction_hypothesis} "
                  f"na czterech kolejnych świecach godzinowych; bez dowodu prospektywnego.")
    # Meaning cannot be inferred from an in-sample clustering exercise.
    applications: list[str] = []
    return {"name": name, "description": conclusion, "applications": applications,
            "historical_p_up": round(historical_p, 6),
            "historical_median_abs_return": round(forward_abs, 8),
            "historical_sample_n": count}



def describe_english(family: Mapping[str, Any]) -> dict[str, Any]:
    """Derive English presentation only; NEVER mutate immutable evidence/family IDs.

    Existing families were frozen with Polish labels, therefore translate their
    *source geometry and frozen exploratory metrics* at public projection time.
    This changes no discovered structure or prospective observation.
    """
    centroid = family.get("centroid") or []
    if len(centroid) < 12:
        return {
            "name_en": f"Autonomous price-path family {family['family_id']}",
            "description_en": "A historical structure hypothesis. Prospective validation has not been established.",
            "applications_en": [],
        }
    path = [float(x) for x in centroid[:12]]
    magnitude = path[-1] - path[0]
    moves = [b - a for a, b in zip(path, path[1:])]
    turns = sum((a > 0) != (b > 0) for a, b in zip(moves, moves[1:]) if a != 0 and b != 0)
    ratio = abs(magnitude) / max(sum(abs(x) for x in moves), 1e-12)
    rhythm = "multi-pulse" if turns >= 5 else "wave-like" if turns >= 3 else "continuous"
    direction = "rising" if magnitude > 0.2 else "falling" if magnitude < -0.2 else "sideways"
    character = "compact" if ratio >= 0.65 else "jagged" if ratio < 0.32 else "variable"
    n = int(family.get("historical_sample_n") or 0)
    p = float(family.get("historical_p_up") or 0.5)
    label = f"{rhythm.capitalize()} {direction} price trace · {character}"
    conclusion = (
        f"This family displays a recurring {rhythm}, {direction} price path with {character} "
        f"geometry. Across {n} historical analogues, the exploratory, smoothed "
        f"frequency of an upward move over the next four hourly market bars was {p:.1%}. "
        "Historical similarity is not proof of prospective predictive performance."
    )
    applications: list[str] = []
    return {"name_en": label, "description_en": conclusion, "applications_en": applications}


def blank_state() -> dict[str, Any]:
    return {"schema_version": VERSION, "families": {}, "snapshots": [],
            "resolutions": [], "lifecycle_events": [], "selection_events": [], "authority": dict(ZERO_AUTHORITY)}


def verify(state: Mapping[str, Any]) -> None:
    if state.get("schema_version") not in {VERSION,LEGACY_VERSION} or state.get("authority") != ZERO_AUTHORITY:
        raise ValueError("Autonomous discovery state authority/schema invalid")
    for key, family in state.get("families", {}).items():
        frozen = {k: v for k, v in family.items() if k != "definition_hash"}
        if family.get("family_id") != key or family.get("definition_hash") != digest(frozen):
            raise ValueError("Immutable family definition changed")
    trial_indices=set(); registered=set(); evaluated=set()
    for section in ("snapshots", "resolutions", "lifecycle_events", "selection_events"):
        prior = "GENESIS"; seen: set[str] = set()
        for row in state.get(section, []):
            body = {k: v for k, v in row.items() if k != "event_hash"}
            if body.get("previous_hash") != prior or row.get("event_hash") != digest(body):
                raise ValueError(f"Broken {section} evidence hash chain")
            sid = row.get("event_id") if section in {"lifecycle_events","selection_events"} else row.get("snapshot_id")
            if not sid or sid in seen:
                raise ValueError(f"Duplicate {section} evidence id")
            if section == "lifecycle_events":
                if row.get("family_id") not in state.get("families", {}):
                    raise ValueError("Lifecycle event refers to an unknown family")
                if row.get("reason") not in {"NO_PROVEN_EDGE_AFTER_40", "REDUNDANT_GEOMETRY"}:
                    raise ValueError("Unrecognized lifecycle reason")
            if section=="selection_events":
                family_id=row.get("family_id"); family=state.get("families",{}).get(family_id)
                if not family or family.get("methodology_version")!=VERSION:raise ValueError("Selection refers to unknown current family")
                index=row.get("trial_index")
                if row.get("action")=="REGISTERED":
                    if family_id in registered:raise ValueError("Duplicate selection registration")
                    if index is not None:
                        if not isinstance(index,int) or not 1<=index<=MAX_SELECTION_TRIALS or index in trial_indices:raise ValueError("Selection budget violation")
                        trial_indices.add(index)
                    if index!=family.get("selection_trial_index") or row.get("target_n")!=REQUIRED_FORWARD_N or row.get("alpha")!=SELECTION_FAMILY_ALPHA:raise ValueError("Selection contract changed")
                    if family.get("selection_target_n")!=REQUIRED_FORWARD_N or family.get("selection_alpha")!=SELECTION_FAMILY_ALPHA or family.get("selection_eligible")!=(index is not None):raise ValueError("Family selection contract invalid")
                    registered.add(family_id)
                elif row.get("action")=="EVALUATED":
                    if family_id not in registered or family_id in evaluated or index is None:raise ValueError("Unregistered/repeated formal evaluation")
                    if index!=family.get("selection_trial_index") or row.get("target_n")!=REQUIRED_FORWARD_N or row.get("alpha")!=SELECTION_FAMILY_ALPHA:raise ValueError("Evaluation contract changed")
                    ids=row.get("holdout_snapshot_ids") or []
                    outcomes={r["snapshot_id"]:r for r in state.get("resolutions",[]) if r.get("family_id")==family_id and r.get("methodology_version")==VERSION}
                    if len(ids)!=REQUIRED_FORWARD_N or len(set(ids))!=len(ids) or any(sid not in outcomes for sid in ids):raise ValueError("Invalid frozen selection holdout")
                    if len(independent_forward_results([outcomes[sid] for sid in ids]))!=REQUIRED_FORWARD_N:raise ValueError("Overlapping selection holdout")
                    evaluated.add(family_id)
                else:raise ValueError("Unknown selection event")
            seen.add(sid); prior = row["event_hash"]
    if {fid for fid,f in state.get("families",{}).items() if f.get("methodology_version")==VERSION}!=registered:raise ValueError("Current family lacks frozen trial registration")


def append_evidence(state: dict[str, Any], section: str, entry: Mapping[str, Any]) -> None:
    rows = state.setdefault(section, [])
    body = {**copy.deepcopy(dict(entry)),
            "previous_hash": rows[-1]["event_hash"] if rows else "GENESIS"}
    rows.append({**body, "event_hash": digest(body)})


def retired_ids(state: Mapping[str, Any]) -> set[str]:
    return {str(e["family_id"]) for e in state.get("lifecycle_events", [])
            if e.get("action") == "RETIRED"}


def selection_contract(state: Mapping[str,Any]) -> dict[str,Any]:
    used=[e["trial_index"] for e in state.get("selection_events",[]) if e.get("action")=="REGISTERED" and e.get("trial_index") is not None]
    index=max(used,default=0)+1
    return {"selection_trial_index":index if index<=MAX_SELECTION_TRIALS else None,
            "selection_target_n":REQUIRED_FORWARD_N,"selection_alpha":SELECTION_FAMILY_ALPHA,
            "selection_eligible":index<=MAX_SELECTION_TRIALS}


def register_trial(state:dict[str,Any],family_id:str,clock:datetime)->None:
    if any(e.get("action")=="REGISTERED" and e.get("family_id")==family_id for e in state.get("selection_events",[])):return
    family=state["families"][family_id]
    append_evidence(state,"selection_events",{"event_id":"register:"+family_id,"action":"REGISTERED","family_id":family_id,
                    "methodology_version":VERSION,"registered_at":clock.isoformat(),"trial_index":family["selection_trial_index"],
                    "target_n":family["selection_target_n"],"alpha":family["selection_alpha"],"execution_authority":False})


def discover(state: dict[str, Any], instrument: str, bars: Sequence[Any], clock: datetime) -> int:
    # Keep the complete immutable memory, including rejected families. This
    # suppresses rediscovery of an unsuccessful hypothesis under a new ID.
    existing = [f for f in state["families"].values() if f["instrument"] == instrument and
                (f.get("methodology_version")==VERSION or f["family_id"] in retired_ids(state))]
    created = 0
    for window in WINDOWS:
        sample = candidates(bars, window)
        groups = learn_groups(sample)
        for group in groups:
            if created >= MAX_NEW_FAMILIES_PER_INSTRUMENT_CYCLE:
                return created
            same_window = [f for f in existing if f["window"] == window]
            nearest = min(((distance(group["centroid"], f["centroid"]), f)
                           for f in same_window), key=lambda pair: pair[0],
                          default=(math.inf, None))
            # Legacy families remain an exclusion zone after retirement.
            if nearest[1] is not None and nearest[0] <= max(group["radius"], nearest[1]["radius"]) * (1.5 if nearest[1]["family_id"] in retired_ids(state) else 1.0):
                continue
            centroid = group["centroid"]
            independent=group.get("independent_members") or independent_episodes(group["members"])
            if len(independent)<MIN_SUPPORT:continue
            family_id = "ASD-" + digest({"methodology_version":VERSION,"instrument": instrument, "window": window,
                                         "centroid": [round(x, 3) for x in centroid]})[:10].upper()
            if family_id in state["families"]:
                continue
            row = {"family_id": family_id, "instrument": instrument, "window": window,
                   "horizon_bars": HORIZON, "discovered_at": clock.isoformat(),
                   "centroid": centroid, "radius": round(group["radius"], 6),
                   "methodology_version":VERSION,"historical_independent_n":len(independent),
                   "methodology": "unsupervised_adaptive_density_medoid_no_named_pattern_templates",
                   **describe(centroid, independent),**selection_contract(state)}
            row["historical_sample_n"]=group["count"]
            # A nearby but distinguishable mode becomes a new candidate branch,
            # not an edit of its ancestor's definition or resolved evidence.
            if nearest[1] is not None and nearest[0] <= 1.5 * max(group["radius"], nearest[1]["radius"]):
                row["parent_family_id"] = nearest[1]["family_id"]
            row["definition_hash"] = digest(row)
            state["families"][family_id] = row
            register_trial(state,family_id,clock)
            existing.append(row)
            created += 1
    return created


def retire_failed_families(state: dict[str, Any], clock: datetime) -> int:
    """Remove falsified hypotheses from the active catalogue, NOT audit history.

    All original family definitions and immutable forecast/outcome evidence
    remain retained, including non-winning hypotheses, to prevent reset abuse.
    """
    finalize_selections(state,clock)
    retired = retired_ids(state)
    count = 0
    for family_id in sorted(state["families"]):
        if family_id in retired or state["families"][family_id].get("methodology_version")!=VERSION:
            continue
        assessment = family_evidence(state, family_id)
        if not assessment["formal_evaluated"]:
            continue
        if assessment["evidence_status"] == "REVIEW_CANDIDATE":
            continue
        append_evidence(state, "lifecycle_events", {
            "event_id": "retirement:" + family_id,
            "family_id": family_id,
            "action": "RETIRED", "reason": "NO_PROVEN_EDGE_AFTER_40",
            "at": clock.isoformat(),
            "evaluated_n": assessment["holdout_n"],
            "directional_improvement": assessment["incremental_brier"],
            "absolute_move_improvement": assessment.get("incremental_abs_move_mae"),
            "production_authority": False,
        })
        retired.add(family_id)
        count += 1
    return count


def reference_observation(bar:Any,clock:datetime)->tuple[float,datetime]|None:
    """A quote has its own source clock; a completed close is a bounded fallback."""
    price=market.finite(bar.close)
    source=getattr(bar,"source_observed_at",None)
    if source is None:
        if market.bar_closed_at(bar)>clock:return None
        source=market.bar_closed_at(bar)
    source=utc(source)
    if price is None or price<=0 or not utc(bar.timestamp)<=source<=clock or clock-source>MAX_REFERENCE_AGE:return None
    return price,source


def capture(state: dict[str, Any], instrument: str, bars: Sequence[Any],
            clock: datetime,reference_bar:Any|None=None) -> bool:
    if len(bars) < max(WINDOWS) + HORIZON:
        return False
    current = bars[-1]
    observed = utc(current.timestamp)
    if market.bar_closed_at(current)>clock or clock-observed>timedelta(hours=3):
        return False
    reference=reference_bar if reference_bar is not None else current
    quote=reference_observation(reference,clock)
    if quote is None:return False
    reference_price,reference_source=quote
    snapshot_id = f"{VERSION}:{instrument}:{observed.isoformat()}"
    if any(r["snapshot_id"] == snapshot_id for r in state["snapshots"]):
        return False
    matches = []; fingerprints={}
    retired = retired_ids(state)
    for window in WINDOWS:
        feature = fingerprint([float(b.close) for b in bars[-window:]])
        fingerprints[str(window)]=list(feature)
        for family in state["families"].values():
            if family.get("methodology_version")!=VERSION or family["family_id"] in retired or family["instrument"] != instrument or family["window"] != window or not feature:
                continue
            d = distance(feature, family["centroid"])
            matches.append((d/max(float(family["radius"]),1e-12),d,family))
    matches.sort(key=lambda x:(x[0],x[2]["family_id"]))
    best=matches[0] if matches else None
    margin=matches[1][0]-best[0] if len(matches)>1 else None
    winner=None; status="FROZEN_NOVEL_UNMATCHED"; reason="NO_SUPPORTED_FAMILY"
    if best is not None:
        if best[0]>1:reason="OUTSIDE_FAMILY_RADIUS"
        elif best[0]>MATCH_MAX_RADIUS_FRACTION or int(best[2].get("historical_independent_n") or 0)<MIN_SUPPORT:
            status="FROZEN_LOW_QUALITY"; reason="INSUFFICIENT_MATCH_QUALITY"
        elif margin is not None and margin<MATCH_MIN_MARGIN:
            status="FROZEN_AMBIGUOUS"; reason="AMBIGUOUS_RUNNER_UP"
        else:winner=best[2]; status="FROZEN_MATCH"; reason="SUPPORTED_SEPARATED_MATCH"
    quality={"best_distance":best[1] if best else None,"normalized_distance":best[0] if best else None,
             "runner_up_gap":margin,"max_radius_fraction":MATCH_MAX_RADIUS_FRACTION,"min_margin":MATCH_MIN_MARGIN,"reason":reason}
    baseline = independent_episodes(candidates(bars, 32))
    baseline_up = sum(r["return"] > 0 for r in baseline)
    baseline_p = (baseline_up + 2) / (len(baseline) + 4) if baseline else 0.5
    baseline_abs = (statistics.median(abs(r["return"]) for r in baseline) if baseline else None)
    payload = {"snapshot_id": snapshot_id, "instrument": instrument,
               "methodology_version":VERSION,
               "observed_at": observed.isoformat(), "frozen_at": clock.isoformat(),
               "query_bar_open_at":observed.isoformat(),"reference_bar_open_at":utc(reference.timestamp).isoformat(),
               "reference_source_observed_at":reference_source.isoformat(),"reference_price":reference_price,
               "query_bar_closed_at":market.bar_closed_at(current).isoformat(),
               "reference_bar_closed_at":market.bar_closed_at(reference).isoformat(),
               "reference_close":reference_price,
               "family_id": winner["family_id"] if winner else None,
               "status":status,"match_quality":quality,"query_fingerprints":fingerprints,
               "p_up": winner["historical_p_up"] if winner else None,
               "baseline_p_up": round(baseline_p, 6),
               "predicted_abs_move": winner["historical_median_abs_return"] if winner else None,
               "baseline_abs_move": round(baseline_abs, 8) if baseline_abs is not None else None,
               "horizon_bars": HORIZON,
               "target_selection":TARGET_SELECTION,"horizon":"4x1h_market_bar_opens_after_freeze",
               "prospective": True, "execution_authority": False}
    append_evidence(state, "snapshots", payload)
    return True


def resolve(state: dict[str, Any], instrument: str,
            bars: Sequence[Any], clock: datetime) -> int:
    outcomes = {x["snapshot_id"] for x in state["resolutions"]}
    count = 0
    for snap in state["snapshots"]:
        if snap.get("methodology_version")!=VERSION or snap.get("target_selection")!=TARGET_SELECTION or snap["instrument"] != instrument or snap["snapshot_id"] in outcomes:
            continue
        future=sorted((bar for bar in bars if utc(bar.timestamp)>=utc(snap["frozen_at"])),key=lambda bar:utc(bar.timestamp))
        if len(future)<HORIZON:continue
        path=future[:HORIZON]; finish=path[-1]
        if market.bar_closed_at(finish)>clock:
            continue
        base = float(snap["reference_close"]); final = float(finish.close)
        if base <= 0 or final <= 0:
            continue
        y = 1 if final > base else 0
        p = snap["p_up"]
        append_evidence(state, "resolutions", {
            "snapshot_id": snap["snapshot_id"], "instrument": instrument,
            "methodology_version":VERSION,"frozen_at":snap["frozen_at"],
            "target_selection":TARGET_SELECTION,"target_bar_opens":[utc(b.timestamp).isoformat() for b in path],
            "family_id": snap["family_id"], "outcome_observed_at": utc(finish.timestamp).isoformat(),
            "outcome_closed_at":market.bar_closed_at(finish).isoformat(),
            "resolved_at": clock.isoformat(), "outcome_up": y, "forward_return": round(final / base - 1, 9),
            "brier": round((p - y) ** 2, 9) if p is not None else None,
            "baseline_brier": round((snap["baseline_p_up"] - y) ** 2, 9) if p is not None else None,
            "abs_move_mae": (round(abs(abs(final / base - 1) - snap["predicted_abs_move"]), 9)
                             if p is not None and snap.get("predicted_abs_move") is not None else None),
            "baseline_abs_move_mae": (round(abs(abs(final / base - 1) - snap["baseline_abs_move"]), 9)
                                      if p is not None and snap.get("baseline_abs_move") is not None else None),
            "prospective": True, "execution_authority": False,
        })
        outcomes.add(snap["snapshot_id"]); count += 1
    return count


def statistically_positive_paired(values: Sequence[float], min_n: int = REQUIRED_FORWARD_N,alpha:float=SELECTION_FAMILY_ALPHA) -> bool:
    """HAC-adjusted one-sided evidence test on strictly prospective paired deltas.

    Adjacent hourly snapshots share a four-bar target and are not independent.
    A lag-3 Newey-West estimate reduces spurious significance from overlap;
    The one-sided alpha is frozen before outcomes and Bonferroni-allocated
    across the lifetime budget of 24 trials and both measured endpoints.
    This is a research-review gate, never proof of trading profitability.
    """
    n = len(values)
    if n < min_n or not all(math.isfinite(float(x)) for x in values):
        return False
    avg = mean(values)
    deviations = [float(x) - avg for x in values]
    gamma0 = sum(v * v for v in deviations) / n
    variance = gamma0
    max_lag = min(HORIZON - 1, n - 1)
    for lag in range(1, max_lag + 1):
        gamma = sum(deviations[i] * deviations[i-lag] for i in range(lag, n)) / n
        variance += 2 * (1 - lag / (max_lag + 1)) * gamma
    stderr = math.sqrt(max(0.0, variance) / n)
    return avg-statistics.NormalDist().inv_cdf(1-alpha)*stderr>0


def independent_forward_results(rows:Sequence[Mapping[str,Any]])->list[Mapping[str,Any]]:
    valid=[r for r in rows if r.get("frozen_at") and r.get("outcome_closed_at") and r.get("resolved_at")
           and utc(r["frozen_at"])<utc(r["outcome_closed_at"])<=utc(r["resolved_at"])]
    ordered=sorted(valid,key=lambda r:(utc(r["outcome_closed_at"]),utc(r["frozen_at"]),str(r["snapshot_id"])))
    selected=[]; previous_end=None
    for row in ordered:
        if previous_end is None or utc(row["frozen_at"])>=previous_end:
            selected.append(row); previous_end=utc(row["outcome_closed_at"])
    return selected


def family_evidence(state: Mapping[str, Any], family_id: str) -> dict[str, Any]:
    family=state["families"][family_id]
    rows = [x for x in state["resolutions"] if x["family_id"] == family_id and x.get("methodology_version")==VERSION and
            x.get("frozen_at") and utc(x["frozen_at"])>=utc(family["discovered_at"]) and
            isinstance(x.get("brier"), (int, float)) and isinstance(x.get("baseline_brier"), (int, float))]
    if family.get("methodology_version")!=VERSION:rows=[]
    n = len(rows)
    independent=independent_forward_results(rows)
    event=next((e for e in state.get("selection_events",[]) if e.get("family_id")==family_id and e.get("action")=="EVALUATED"),None)
    selected=independent[:REQUIRED_FORWARD_N]
    if event is not None:
        by_id={r["snapshot_id"]:r for r in rows}
        selected=[by_id[sid] for sid in event["holdout_snapshot_ids"]]
    eligible=family.get("selection_eligible") is True and family.get("selection_trial_index") is not None
    formal=eligible and len(selected)==REQUIRED_FORWARD_N
    gains = [x["baseline_brier"] - x["brier"] for x in selected]
    directional_pass = formal and statistically_positive_paired(gains,alpha=float(family.get("selection_alpha",SELECTION_FAMILY_ALPHA)))
    move_rows = [x for x in selected if isinstance(x.get("abs_move_mae"), (float, int))
                 and isinstance(x.get("baseline_abs_move_mae"), (float, int))]
    movement_gains = [x["baseline_abs_move_mae"] - x["abs_move_mae"] for x in move_rows]
    movement_pass = formal and statistically_positive_paired(movement_gains,alpha=float(family.get("selection_alpha",SELECTION_FAMILY_ALPHA)))
    if event is not None:
        directional_pass=event["directional_pass"]; movement_pass=event["absolute_move_pass"]
    status = ("REVIEW_CANDIDATE" if directional_pass or movement_pass else
              "NO_PROVEN_EDGE" if formal else
              "EXPLORATORY_NO_TRIAL_BUDGET" if not eligible else
              "PROSPECTIVE_COLLECTION" if n else "HISTORICAL_DISCOVERY_ONLY")
    return {"prospective_n": n,"independent_n":len(independent),"required_independent_n":REQUIRED_FORWARD_N,
            "holdout_n":len(selected),"formal_evaluated":formal,
            "selection_trial_index":family.get("selection_trial_index"),"selection_alpha":family.get("selection_alpha"),
            "holdout_snapshot_ids":[r["snapshot_id"] for r in selected],
            "mean_brier": round(mean([r["brier"] for r in selected]), 6) if selected else None,
            "paired_baseline_brier": round(mean([r["baseline_brier"] for r in selected]), 6) if selected else None,
            "incremental_brier": round(mean(gains), 6) if n else None,
            "prospective_abs_move_n": len(move_rows),
            "incremental_abs_move_mae": round(mean(movement_gains), 8) if movement_gains else None,
            "directional_pass": directional_pass, "absolute_move_pass": movement_pass,
            "evidence_status": status, "review_only": True}


def finalize_selections(state:dict[str,Any],clock:datetime)->int:
    done={e["family_id"] for e in state.get("selection_events",[]) if e.get("action")=="EVALUATED"}
    count=0
    for family in sorted(state["families"].values(),key=lambda f:f["family_id"]):
        fid=family["family_id"]
        if family.get("methodology_version")!=VERSION or fid in done:continue
        score=family_evidence(state,fid)
        if not score["formal_evaluated"]:continue
        append_evidence(state,"selection_events",{"event_id":"evaluate:"+fid,"action":"EVALUATED","family_id":fid,
                        "methodology_version":VERSION,"evaluated_at":clock.isoformat(),"trial_index":family["selection_trial_index"],
                        "target_n":REQUIRED_FORWARD_N,"alpha":family["selection_alpha"],"holdout_snapshot_ids":score["holdout_snapshot_ids"],
                        "directional_pass":score["directional_pass"],"absolute_move_pass":score["absolute_move_pass"],"execution_authority":False})
        count+=1
    return count


def publication(state: Mapping[str, Any], clock: datetime, errors: Mapping[str, Any] | None = None) -> dict[str, Any]:
    resolved = {r["snapshot_id"] for r in state["resolutions"]}
    current = []
    for instrument in market.DEFAULT_INSTRUMENTS:
        snapshots = [x for x in state["snapshots"] if x["instrument"] == instrument and x.get("methodology_version")==VERSION]
        if snapshots:
            snap = snapshots[-1]
            current.append({"instrument": instrument, "observed_at": snap["observed_at"],
                            "query_bar_open_at":snap.get("query_bar_open_at"),"frozen_at":snap.get("frozen_at"),
                            "reference_price":snap.get("reference_price"),"reference_source_observed_at":snap.get("reference_source_observed_at"),
                            "target_selection":snap.get("target_selection"),"horizon":snap.get("horizon"),
                            "family_id": snap["family_id"], "status": snap["status"],
                            "methodology_version":VERSION,"match_quality":copy.deepcopy(snap.get("match_quality")),
                            "prospective": snap["snapshot_id"] in resolved,
                            "p_up": snap["p_up"]})
    rows = []
    retired = retired_ids(state)
    for f in state["families"].values():
        if f["family_id"] in retired or f.get("methodology_version")!=VERSION:
            continue
        evidence = family_evidence(state, f["family_id"])
        evidence.pop("holdout_snapshot_ids",None)
        # Meaning is unknown until prospective evidence beats a frozen baseline.
        # Never publish legacy template applications even though preserved in history.
        meaning_pl = []
        meaning_en = []
        if evidence["evidence_status"] == "REVIEW_CANDIDATE":
            if evidence["directional_pass"]:
                meaning_pl.append("Kandydat badawczy: filtr kierunku na horyzoncie 4 świec 1h; wymaga niezależnego przeglądu.")
                meaning_en.append("Research candidate: four-hour direction filter; requires independent review.")
            if evidence["absolute_move_pass"]:
                meaning_pl.append("Kandydat badawczy: ocena bezwzględnego rozmiaru ruchu; wymaga niezależnego przeglądu.")
                meaning_en.append("Research candidate: absolute movement size estimate; requires independent review.")
        rows.append({k: copy.deepcopy(f[k]) for k in (
            "family_id", "name", "instrument", "window", "discovered_at",
            "description", "applications", "historical_sample_n", "historical_p_up",
            "historical_median_abs_return", "methodology","methodology_version","historical_independent_n")} |
                    describe_english(f) | evidence |
                    {"applications": meaning_pl, "applications_en": meaning_en,
                     "application_status": ("RESEARCH_CANDIDATE" if meaning_pl else "UNDETERMINED"),
                     "shape_points": list(f["centroid"][:12]),
                     "parent_family_id": f.get("parent_family_id")})
    rows.sort(key=lambda x: (x["evidence_status"] != "REVIEW_CANDIDATE",
                              -x["prospective_n"], -x["historical_sample_n"], x["family_id"]))
    # Do not show a retired family as the current recommendation.
    current = [dict(x, family_id=None, status="RETIRED_NOT_MATCHED")
               if x["family_id"] in retired else x for x in current]
    legacy_families=[]
    for f in state["families"].values():
        if f.get("methodology_version")==VERSION:continue
        old_rows=[r for r in state["resolutions"] if r.get("family_id")==f["family_id"] and r.get("methodology_version")!=VERSION]
        scored=[r for r in old_rows if isinstance(r.get("brier"),(int,float)) and isinstance(r.get("baseline_brier"),(int,float))]
        archive={key:copy.deepcopy(f.get(key)) for key in ("family_id","name","instrument","window","discovered_at","description","applications",
                    "historical_sample_n","historical_p_up","historical_median_abs_return","methodology","parent_family_id")}
        archive.update(describe_english(f))
        archive.update({"methodology_version":f.get("methodology_version",LEGACY_VERSION),"evidence_status":"ARCHIVED_LEGACY",
                        "application_status":"ARCHIVED_HISTORICAL","included_in_current_metrics":False,"review_only":True,
                        "prospective_n":len(old_rows),"legacy_prospective_n":len(old_rows),
                        "mean_brier":round(mean([r["brier"] for r in scored]),6) if scored else None,
                        "paired_baseline_brier":round(mean([r["baseline_brier"] for r in scored]),6) if scored else None,
                        "incremental_brier":round(mean([r["baseline_brier"]-r["brier"] for r in scored]),6) if scored else None,
                        "shape_points":list(f.get("centroid",[])[:12])})
        legacy_families.append(archive)
    legacy_snapshots=sum(s.get("methodology_version")!=VERSION for s in state["snapshots"])
    legacy_resolutions=sum(s.get("methodology_version")!=VERSION for s in state["resolutions"])
    used=sum(e.get("action")=="REGISTERED" and e.get("trial_index") is not None for e in state.get("selection_events",[]))
    return {"schema_version": VERSION,"methodology_version":VERSION, "mode": "SHADOW_ONLY", "generated_at": clock.isoformat(),
            "engine": "FSE Autonomous Structure Discovery", "authority": dict(ZERO_AUTHORITY),
            "automatic_promotion": False, "production_impact": False,
            "discovery_definition": "Unsupervised temporal clustering; no fixed pattern labels.",
            "evidence_definition": "Historical examples describe hypotheses only. Prospective 4x1h outcomes alone count as validation.",
            "minimum_forward_n": REQUIRED_FORWARD_N,
            "legacy_evidence":{"families":len(legacy_families),"snapshots":legacy_snapshots,"resolutions":legacy_resolutions,"included_in_current_metrics":False},
            "legacy_families":legacy_families,
            "summary": {"families": len(rows),
                        "legacy_families":len(legacy_families),"selection_trial_budget":MAX_SELECTION_TRIALS,
                        "selection_trials_used":used,"selection_trials_remaining":MAX_SELECTION_TRIALS-used,
                        "retired_families": len(retired),
                        "historical_only": sum(x["prospective_n"] == 0 for x in rows),
                        "review_candidates": sum(x["evidence_status"] == "REVIEW_CANDIDATE" for x in rows),
                        "frozen_snapshots": len(state["snapshots"])-legacy_snapshots,
                        "prospective_resolutions": len(state["resolutions"])-legacy_resolutions,
                        "matched_resolutions": sum(x["family_id"] is not None and x.get("methodology_version")==VERSION for x in state["resolutions"])},
            "current_matches": current, "families": rows, "source_errors": dict(errors or {})}


def cycle(state: dict[str, Any], histories: Mapping[str, Sequence[Any]], clock: datetime,live_clock:bool=False) -> dict[str, Any]:
    verify(state)
    state["schema_version"]=VERSION
    state.setdefault("selection_events",[])
    for instrument, raw in histories.items():
        as_of=now_utc() if live_clock else clock
        bars = sorted((b for b in raw if market.bar_closed_at(b)<=as_of), key=lambda b: utc(b.timestamp))
        if len(bars) < 128:
            continue
        # The current forecast is frozen against a catalog trained on historical
        # bars. Existing prospective evidence resolves independently afterward.
        resolve(state, instrument, bars, as_of)
        retire_failed_families(state, as_of)
        discover(state, instrument, bars, as_of)
        frozen_at=now_utc() if live_clock else clock
        available=[bar for bar in raw if reference_observation(bar,frozen_at) is not None]
        reference=max(available,key=lambda bar:(utc(bar.timestamp),reference_observation(bar,frozen_at)[1])) if available else None
        if reference is not None:capture(state, instrument, bars, frozen_at,reference_bar=reference)
    verify(state)
    return state


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp.replace(path)


def run(state_dir: Path, public: Path, clock: datetime,live_clock:bool=False) -> dict[str, Any]:
    state_path = state_dir / STATE_NAME
    state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.is_file() else blank_state()
    verify(state)
    client = market.YahooChartClient()
    histories: dict[str, list[Any]] = {}
    errors = {}
    for instrument, symbol in market.DEFAULT_INSTRUMENTS.items():
        try:
            bars = client.bars(symbol, "730d", "60m")
            if len(bars) < 128:
                raise ValueError("Insufficient hourly bars")
            histories[instrument] = bars
        except Exception as exc:
            errors[instrument] = str(exc)[:180]
    if not histories:
        raise RuntimeError("No independently sourced hourly market bars; keep last published artifact")
    cycle(state, histories, clock,live_clock=live_clock)
    result = publication(state, now_utc() if live_clock else clock, errors)
    write_json(state_path, state)
    write_json(public, result)
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--public", type=Path, default=PUBLIC_PATH)
    parser.add_argument("--verify", action="store_true")
    parser.add_argument("--now")
    args = parser.parse_args()
    state_path = args.state_dir / STATE_NAME
    if args.verify:
        verify(json.loads(state_path.read_text(encoding="utf-8")))
        print("FSE_ASD_HASH_CHAIN_AND_AUTHORITY_PASS")
        return
    result = run(args.state_dir, args.public, utc(args.now) if args.now else now_utc(),live_clock=args.now is None)
    print(canonical({"families": result["summary"]["families"],
                     "prospective_resolutions": result["summary"]["prospective_resolutions"],
                     "errors": result["source_errors"]}))


if __name__ == "__main__":
    main()
