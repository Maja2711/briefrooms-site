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

VERSION = "FSE-ASD-1.0"
HORIZON = 4
WINDOWS = (32, 64)
MIN_SUPPORT = 6
MAX_FAMILIES_PER_INSTRUMENT = 14
MAX_CANDIDATES = 420
REQUIRED_FORWARD_N = 40
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


def candidates(bars: Sequence[Any], window: int) -> list[dict[str, Any]]:
    """Training windows must have HORIZON *historical* forward bars available.

    Future outcomes are used only for exploratory conditional statistics,
    never for unsupervised grouping or prospective credit.
    """
    end_max = len(bars) - 1 - HORIZON
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
        rows.append({"vector": vector, "end_at": bars[end].timestamp.isoformat(),
                     "return": future / base - 1, "window": window})
    return rows


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

    def root(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for i in range(n):
        for j in range(i + 1, n):
            if dists[i][j] <= eps:
                ri, rj = root(i), root(j)
                if ri != rj:
                    parent[max(ri, rj)] = min(ri, rj)
    components: dict[int, list[int]] = {}
    for i in range(n):
        components.setdefault(root(i), []).append(i)
    clusters = []
    for component in components.values():
        if len(component) < MIN_SUPPORT:
            continue
        center = min(component, key=lambda i: mean([dists[i][j] for j in component]))
        retained = [i for i in component if dists[center][i] <= eps]
        if len(retained) < MIN_SUPPORT:
            continue
        # Never include outcome values in finding the cluster.
        clusters.append({"centroid": list(vectors[center]), "radius": eps,
                         "members": [rows[i] for i in retained],
                         "count": len(retained)})
    return sorted(clusters, key=lambda x: (-x["count"], digest(x["centroid"])))


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
    applications = [
        "Hipoteza badawcza: rozpoznawanie podobnego reżimu ceny i odmiennych warunków wejścia.",
        ("Hipoteza badawcza: filtr prawdopodobieństwa kierunku w horyzoncie 4 świec 1h."
         if abs(historical_p - 0.5) >= 0.06 else
         "Hipoteza badawcza: filtr neutralności kierunkowej, bez wymuszania LONG/SHORT."),
        "Hipoteza badawcza: ocena ryzyka ruchu i niekorzystnego odchylenia; wymaga walidacji forward.",
    ]
    return {"name": name, "description": conclusion, "applications": applications,
            "historical_p_up": round(historical_p, 6),
            "historical_median_abs_return": round(forward_abs, 8),
            "historical_sample_n": count}


def blank_state() -> dict[str, Any]:
    return {"schema_version": VERSION, "families": {}, "snapshots": [],
            "resolutions": [], "authority": dict(ZERO_AUTHORITY)}


def verify(state: Mapping[str, Any]) -> None:
    if state.get("schema_version") != VERSION or state.get("authority") != ZERO_AUTHORITY:
        raise ValueError("Autonomous discovery state authority/schema invalid")
    for key, family in state.get("families", {}).items():
        frozen = {k: v for k, v in family.items() if k != "definition_hash"}
        if family.get("family_id") != key or family.get("definition_hash") != digest(frozen):
            raise ValueError("Immutable family definition changed")
    for section in ("snapshots", "resolutions"):
        prior = "GENESIS"; seen: set[str] = set()
        for row in state.get(section, []):
            body = {k: v for k, v in row.items() if k != "event_hash"}
            if body.get("previous_hash") != prior or row.get("event_hash") != digest(body):
                raise ValueError(f"Broken {section} evidence hash chain")
            sid = row.get("snapshot_id")
            if not sid or sid in seen:
                raise ValueError(f"Duplicate {section} snapshot id")
            seen.add(sid); prior = row["event_hash"]


def append_evidence(state: dict[str, Any], section: str, entry: Mapping[str, Any]) -> None:
    rows = state[section]
    body = {**copy.deepcopy(dict(entry)),
            "previous_hash": rows[-1]["event_hash"] if rows else "GENESIS"}
    rows.append({**body, "event_hash": digest(body)})


def discover(state: dict[str, Any], instrument: str, bars: Sequence[Any], clock: datetime) -> int:
    existing = [f for f in state["families"].values() if f["instrument"] == instrument]
    created = 0
    for window in WINDOWS:
        sample = candidates(bars, window)
        groups = learn_groups(sample)
        for group in groups:
            if len(existing) >= MAX_FAMILIES_PER_INSTRUMENT:
                break
            nearest = min((distance(group["centroid"], f["centroid"])
                           for f in existing if f["window"] == window), default=math.inf)
            if nearest <= group["radius"]:
                continue
            centroid = group["centroid"]
            family_id = "ASD-" + digest({"instrument": instrument, "window": window,
                                         "centroid": [round(x, 3) for x in centroid]})[:10].upper()
            if family_id in state["families"]:
                continue
            row = {"family_id": family_id, "instrument": instrument, "window": window,
                   "horizon_bars": HORIZON, "discovered_at": clock.isoformat(),
                   "centroid": centroid, "radius": round(group["radius"], 6),
                   "methodology": "unsupervised_adaptive_density_medoid_no_named_pattern_templates",
                   **describe(centroid, group["members"])}
            row["definition_hash"] = digest(row)
            state["families"][family_id] = row
            existing.append(row)
            created += 1
    return created


def capture(state: dict[str, Any], instrument: str, bars: Sequence[Any],
            clock: datetime) -> bool:
    if len(bars) < max(WINDOWS) + HORIZON:
        return False
    current = bars[-1]
    observed = utc(current.timestamp)
    if observed + timedelta(hours=1) > clock or clock - observed > timedelta(hours=3):
        return False
    snapshot_id = f"{instrument}:{observed.isoformat()}"
    if any(r["snapshot_id"] == snapshot_id for r in state["snapshots"]):
        return False
    matches = []
    for window in WINDOWS:
        feature = fingerprint([float(b.close) for b in bars[-window:]])
        for family in state["families"].values():
            if family["instrument"] != instrument or family["window"] != window or not feature:
                continue
            d = distance(feature, family["centroid"])
            if d <= family["radius"]:
                matches.append((d, family))
    winner = min(matches, key=lambda x: (x[0], x[1]["family_id"]))[1] if matches else None
    baseline = candidates(bars, 32)
    baseline_up = sum(r["return"] > 0 for r in baseline)
    baseline_p = (baseline_up + 2) / (len(baseline) + 4) if baseline else 0.5
    payload = {"snapshot_id": snapshot_id, "instrument": instrument,
               "observed_at": observed.isoformat(), "frozen_at": clock.isoformat(),
               "reference_close": float(current.close),
               "family_id": winner["family_id"] if winner else None,
               "status": "FROZEN_MATCH" if winner else "FROZEN_NOVEL_UNMATCHED",
               "p_up": winner["historical_p_up"] if winner else None,
               "baseline_p_up": round(baseline_p, 6), "horizon_bars": HORIZON,
               "prospective": True, "execution_authority": False}
    append_evidence(state, "snapshots", payload)
    return True


def resolve(state: dict[str, Any], instrument: str,
            bars: Sequence[Any], clock: datetime) -> int:
    outcomes = {x["snapshot_id"] for x in state["resolutions"]}
    idx = {utc(row.timestamp): i for i, row in enumerate(bars)}
    count = 0
    for snap in state["snapshots"]:
        if snap["instrument"] != instrument or snap["snapshot_id"] in outcomes:
            continue
        ref_idx = idx.get(utc(snap["observed_at"]))
        if ref_idx is None or ref_idx + HORIZON >= len(bars):
            continue
        finish = bars[ref_idx + HORIZON]
        if utc(finish.timestamp) + timedelta(hours=1) > clock:
            continue
        base = float(snap["reference_close"]); final = float(finish.close)
        if base <= 0 or final <= 0:
            continue
        y = 1 if final > base else 0
        p = snap["p_up"]
        append_evidence(state, "resolutions", {
            "snapshot_id": snap["snapshot_id"], "instrument": instrument,
            "family_id": snap["family_id"], "outcome_observed_at": utc(finish.timestamp).isoformat(),
            "resolved_at": clock.isoformat(), "outcome_up": y, "forward_return": round(final / base - 1, 9),
            "brier": round((p - y) ** 2, 9) if p is not None else None,
            "baseline_brier": round((snap["baseline_p_up"] - y) ** 2, 9) if p is not None else None,
            "prospective": True, "execution_authority": False,
        })
        outcomes.add(snap["snapshot_id"]); count += 1
    return count


def family_evidence(state: Mapping[str, Any], family_id: str) -> dict[str, Any]:
    rows = [x for x in state["resolutions"] if x["family_id"] == family_id and x["brier"] is not None]
    n = len(rows)
    gains = [x["baseline_brier"] - x["brier"] for x in rows]
    advantage = mean(gains) if n else None
    stderr = statistics.stdev(gains) / math.sqrt(n) if n > 1 else math.inf
    review = n >= REQUIRED_FORWARD_N and (advantage or 0) - 1.96 * stderr > 0
    status = ("REVIEW_CANDIDATE" if review else
              "NO_PROVEN_EDGE" if n >= REQUIRED_FORWARD_N else
              "PROSPECTIVE_COLLECTION" if n else "HISTORICAL_DISCOVERY_ONLY")
    return {"prospective_n": n, "mean_brier": round(mean([r["brier"] for r in rows]), 6) if n else None,
            "paired_baseline_brier": round(mean([r["baseline_brier"] for r in rows]), 6) if n else None,
            "incremental_brier": round(advantage, 6) if n else None,
            "evidence_status": status, "review_only": True}


def publication(state: Mapping[str, Any], clock: datetime, errors: Mapping[str, Any] | None = None) -> dict[str, Any]:
    resolved = {r["snapshot_id"] for r in state["resolutions"]}
    current = []
    for instrument in market.DEFAULT_INSTRUMENTS:
        snapshots = [x for x in state["snapshots"] if x["instrument"] == instrument]
        if snapshots:
            snap = snapshots[-1]
            current.append({"instrument": instrument, "observed_at": snap["observed_at"],
                            "family_id": snap["family_id"], "status": snap["status"],
                            "prospective": snap["snapshot_id"] in resolved,
                            "p_up": snap["p_up"]})
    rows = []
    for f in state["families"].values():
        evidence = family_evidence(state, f["family_id"])
        rows.append({k: copy.deepcopy(f[k]) for k in (
            "family_id", "name", "instrument", "window", "discovered_at",
            "description", "applications", "historical_sample_n", "historical_p_up",
            "historical_median_abs_return", "methodology")} | evidence)
    rows.sort(key=lambda x: (x["evidence_status"] != "REVIEW_CANDIDATE",
                              -x["prospective_n"], -x["historical_sample_n"], x["family_id"]))
    return {"schema_version": VERSION, "mode": "SHADOW_ONLY", "generated_at": clock.isoformat(),
            "engine": "FSE Autonomous Structure Discovery", "authority": dict(ZERO_AUTHORITY),
            "automatic_promotion": False, "production_impact": False,
            "discovery_definition": "Unsupervised temporal clustering; no fixed pattern labels.",
            "evidence_definition": "Historical examples describe hypotheses only. Prospective 4x1h outcomes alone count as validation.",
            "minimum_forward_n": REQUIRED_FORWARD_N,
            "summary": {"families": len(rows),
                        "historical_only": sum(x["prospective_n"] == 0 for x in rows),
                        "review_candidates": sum(x["evidence_status"] == "REVIEW_CANDIDATE" for x in rows),
                        "frozen_snapshots": len(state["snapshots"]),
                        "prospective_resolutions": len(state["resolutions"]),
                        "matched_resolutions": sum(x["family_id"] is not None for x in state["resolutions"])},
            "current_matches": current, "families": rows, "source_errors": dict(errors or {})}


def cycle(state: dict[str, Any], histories: Mapping[str, Sequence[Any]], clock: datetime) -> dict[str, Any]:
    verify(state)
    for instrument, raw in histories.items():
        bars = sorted((b for b in raw if utc(b.timestamp) + timedelta(hours=1) <= clock), key=lambda b: utc(b.timestamp))
        if len(bars) < 128:
            continue
        # The current forecast is frozen against a catalog trained on historical
        # bars. Existing prospective evidence resolves independently afterward.
        resolve(state, instrument, bars, clock)
        discover(state, instrument, bars, clock)
        capture(state, instrument, bars, clock)
    verify(state)
    return state


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp.replace(path)


def run(state_dir: Path, public: Path, clock: datetime) -> dict[str, Any]:
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
    cycle(state, histories, clock)
    result = publication(state, clock, errors)
    write_json(state_path, state)
    write_json(public, result)
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--public", type=Path, default=PUBLIC_PATH)
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    state_path = args.state_dir / STATE_NAME
    if args.verify:
        verify(json.loads(state_path.read_text(encoding="utf-8")))
        print("FSE_ASD_HASH_CHAIN_AND_AUTHORITY_PASS")
        return
    result = run(args.state_dir, args.public, now_utc())
    print(canonical({"families": result["summary"]["families"],
                     "prospective_resolutions": result["summary"]["prospective_resolutions"],
                     "errors": result["source_errors"]}))


if __name__ == "__main__":
    main()
