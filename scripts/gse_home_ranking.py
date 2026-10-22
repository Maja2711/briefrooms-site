"""Select the largest *eligible* GSE v2 forecast probabilities for the homepage.

Rank real directional hypotheses, not complements of rare-event probabilities.
Only sanitized frozen forecasts may leave Shadow; no raw evidence is published.
"""
from __future__ import annotations
import json
import math
from datetime import datetime, timezone
from pathlib import Path

H = {24: "24h", 168: "7d", 720: "30d", 2160: "90d"}
ASSETS = {
    "BRENT": ("cena ropy Brent", "Brent crude price", "Brent"),
    "WTI": ("cena ropy WTI", "WTI crude price", "WTI"),
    "GOLD": ("cena złota", "gold price", "Gold"),
    "COPPER": ("cena miedzi", "copper price", "Copper"),
    "WHEAT": ("cena pszenicy", "wheat price", "Wheat"),
    "NATGAS": ("cena gazu ziemnego", "natural gas price", "Natural gas"),
    "SPX": ("indeks S&P 500 (proxy SPY)", "S&P 500 index (SPY proxy)", "S&P 500"),
    "US10Y": ("rentowność 10-letnich obligacji USA", "US 10-year Treasury yield", "US 10Y"),
    "USD": ("indeks dolara amerykańskiego", "US Dollar Index", "USD index"),
}
EVENTS = {
    "russia_nato_attack_any_eastern_flank": ("Atak zbrojny Rosji na wschodnią flankę NATO", "Russian armed attack on NATO's eastern flank", "NATO flank"),
    "russia_attack_poland": ("Atak zbrojny Rosji na Polskę", "Russian armed attack on Poland", "Poland"),
    "russia_attack_lithuania": ("Atak zbrojny Rosji na Litwę", "Russian armed attack on Lithuania", "Lithuania"),
    "russia_attack_latvia": ("Atak zbrojny Rosji na Łotwę", "Russian armed attack on Latvia", "Latvia"),
    "russia_attack_estonia": ("Atak zbrojny Rosji na Estonię", "Russian armed attack on Estonia", "Estonia"),
}

def _read_json(path):
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}

def _read_jsonl(path):
    if not path.is_file():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
        except (ValueError, TypeError):
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows

def _dt(value):
    try:
        at = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return at.astimezone(timezone.utc) if at.tzinfo else None
    except (ValueError, TypeError, OverflowError):
        return None

def _num(value):
    if isinstance(value, bool):
        return None
    try:
        n = float(value)
    except (ValueError, TypeError):
        return None
    return n if math.isfinite(n) else None

def _current(row, now, max_age_h):
    at, target = _dt(row.get("forecast_at")), _dt(row.get("target_at"))
    if at is None or target is None:
        return False
    age = (now - at).total_seconds()
    return -120 <= age <= max_age_h * 3600 and target > now

def _latest_by(rows, keyfn):
    latest = {}
    for row in rows:
        at, key = _dt(row.get("forecast_at")), keyfn(row)
        if key is not None and at is not None and (key not in latest or at > latest[key][0]):
            latest[key] = (at, row)
    return [x[1] for x in latest.values()]

def _market(state_dir, now):
    rows = _latest_by(_read_jsonl(state_dir / "gse_v2_regime_forecasts.jsonl"),
        lambda r: (str(r.get("asset")), str(r.get("horizon_hours"))) if str(r.get("asset")) in ASSETS else None)
    out = []
    for row in rows:
        asset = str(row.get("asset"))
        try:
            horizon, direction = int(row.get("horizon_hours")), int(row.get("direction"))
        except (ValueError, TypeError):
            continue
        p, conf, n = _num(row.get("v2_regime_candidate_probability")), _num(row.get("epistemic_confidence")), _num(row.get("effective_cluster_n"))
        if (horizon not in (24, 168, 720) or direction not in (-1, 1)
                or p is None or not .5 <= p <= .85
                or conf is None or not 0 <= conf <= 1 or n is None or n < 5
                or not _current(row, now, 12)):
            continue
        pl, en, label = ASSETS[asset]
        up = direction == 1
        out.append({
            "kind": "market_direction", "asset": asset, "short_label_pl": label + (" ↑" if up else " ↓"),
            "short_label_en": label + (" ↑" if up else " ↓"),
            "question_pl": f"Czy {pl} {'wzrośnie' if up else 'spadnie'} w ciągu {H[horizon]}?",
            "question_en": f"Will the {en} {'rise' if up else 'fall'} over {H[horizon]}?",
            "probability": round(p, 6), "confidence": round(conf, 6),
            "sample_size": int(n), "sample_label_pl": "N efektywne", "sample_label_en": "Effective N",
            "horizon_hours": horizon, "horizon_label": H[horizon],
            "forecast_at": row["forecast_at"], "target_at": row["target_at"], "max_age_hours": 12,
            "href_pl": "/pl/geo/gse-lab.html", "href_en": "/en/geo/gse-lab.html"
        })
    return out

def _events(state_dir, now):
    state = _read_json(state_dir / "gse_event_threat_state.json")
    cal = _read_json(state_dir / "gse_event_probability_calibration.json").get("overall") or {}
    count, positives = int(cal.get("count") or 0), int(cal.get("positive_count") or 0)
    brier, prior = _num(cal.get("mean_brier")), _num(cal.get("mean_prior_brier"))
    if (not state.get("model_status") or "uncalibrated" in str(state["model_status"])
            or count < 30 or positives < 5 or brier is None or prior is None or brier > prior):
        return []
    rows = _latest_by(_read_jsonl(state_dir / "gse_event_probability_forecasts.jsonl"),
        lambda r: (str(r.get("event_type")), str(r.get("horizon_hours"))) if str(r.get("event_type")) in EVENTS else None)
    out = []
    for row in rows:
        try:
            horizon = int(row.get("horizon_hours"))
        except (ValueError, TypeError):
            continue
        p, conf = _num(row.get("predicted_probability")), _num(row.get("confidence"))
        if (horizon not in (168, 720, 2160) or p is None or not 0 <= p <= 1
                or conf is None or not 0 <= conf <= 1 or not _current(row, now, 30)):
            continue
        pl, en, short = EVENTS[str(row["event_type"])]
        out.append({
            "kind": "event", "asset": "EVENT", "short_label_pl": short, "short_label_en": short,
            "question_pl": f"{pl} w ciągu {H[horizon]}?",
            "question_en": f"{en} within {H[horizon]}?",
            "probability": round(p, 6), "confidence": round(conf, 6),
            "sample_size": count, "sample_label_pl": "N rozstrzygnięć", "sample_label_en": "Resolved N",
            "horizon_hours": horizon, "horizon_label": H[horizon],
            "forecast_at": row["forecast_at"], "target_at": row["target_at"], "max_age_hours": 30,
            "href_pl": "/pl/geo/gse-lab.html", "href_en": "/en/geo/gse-lab.html"
        })
    return out

def rank_home_forecasts(state_dir: Path, *, generated_at: str, limit: int = 3) -> dict:
    now = _dt(generated_at)
    if now is None:
        raise ValueError("generated_at must be timezone aware")
    rows = _market(state_dir, now) + _events(state_dir, now)
    rows.sort(key=lambda r: (-r["probability"], -r["confidence"], -_dt(r["forecast_at"]).timestamp(), r["asset"]))
    unique = {}
    for row in rows:
        subject = row["asset"] if row["kind"] == "market_direction" else row["short_label_en"]
        unique.setdefault(row["kind"] + ":" + subject, row)
    winners = list(unique.values())[:max(1, min(5, limit))]
    return {
        "schema_version": "gse-v2-home-ranking-v1",
        "generated_at": generated_at,
        "selection_policy": "largest_probability_among_fresh_latest_per_subject",
        "eligible_subjects": len(unique),
        "selected": winners[0] if winners else None,
        "runners_up": winners[1:],
        "research_only": True, "decision_influence": False, "automatic_promotion": False
    }
