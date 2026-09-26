#!/usr/bin/env python3
"""Build sanitized BriefRooms Decision LAB public projection from Belief Core shadow state."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from evidence_pattern_discovery import build_pattern_report


def load(path: Path, default):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def records(value):
    """Accept persisted Belief Core collections stored as either list or dict."""
    if isinstance(value, dict):
        return list(value.values())
    if isinstance(value, list):
        return value
    return []


def build_payload(state, report):
    forecasts = records(state.get("forecasts"))
    definitions = records(state.get("definitions"))
    state = dict(state)
    state["definitions_by_id"] = {str(x.get("belief_id")): x for x in definitions if x.get("belief_id")}
    verifications = records(state.get("verifications"))
    verified = {
        str(v.get("forecast_id")): v
        for v in verifications
        if v.get("forecast_id")
    }
    def horizon_label(f):
        meta = f.get("metadata") or {}
        if meta.get("research_horizon_label"):
            return str(meta["research_horizon_label"])
        h = round(float(f.get("horizon_hours") or 0))
        return {3:"3H", 12:"12H", 24:"24H", 72:"3D", 120:"5D"}.get(h, f"{h}H")

    def row_for(f):
        fid = str(f.get("forecast_id", ""))
        v = verified.get(fid)
        return {
            "forecast_id": fid, "entity": f.get("entity"), "belief_id": f.get("belief_id"),
            "claim": (state.get("definitions_by_id") or {}).get(str(f.get("belief_id")), {}).get("claim"),
            "probability": f.get("predicted_probability"), "confidence": f.get("forecast_confidence"),
            "forecast_at": f.get("forecast_at"), "target_at": f.get("target_at"),
            "horizon_hours": f.get("horizon_hours"), "horizon_label": horizon_label(f),
            "regime": f.get("regime"), "status": "RESOLVED" if v else "OPEN",
            "outcome": None if not v else bool(v.get("outcome")),
            "brier_score": None if not v else v.get("brier_score"),
        }

    multi = [f for f in forecasts if (f.get("metadata") or {}).get("multihorizon_contract") == "decision-lab-multihorizon-v1"]
    primary = [f for f in multi if (f.get("metadata") or {}).get("primary_research_horizon")]
    legacy = [f for f in forecasts if f not in multi]
    display_source = primary + legacy
    rows = [row_for(f) for f in sorted(display_source, key=lambda x: str(x.get("forecast_at", "")), reverse=True)[:40]]

    grouped = {}
    for f in multi:
        key = (str(f.get("belief_id")), str(f.get("forecast_at")))
        grouped.setdefault(key, []).append(row_for(f))
    multihorizon_paths = []
    for (belief_id, forecast_at), path in sorted(grouped.items(), key=lambda kv: kv[0][1], reverse=True)[:40]:
        multihorizon_paths.append({
            "belief_id": belief_id, "forecast_at": forecast_at,
            "horizons": sorted(path, key=lambda x: float(x.get("horizon_hours") or 0)),
        })

    horizon_stats = []
    by_h = {}
    for f in multi:
        v = verified.get(str(f.get("forecast_id", "")))
        if not v:
            continue
        label = horizon_label(f)
        by_h.setdefault(label, []).append(float(v.get("brier_score")))
    for label in ("3H","12H","24H","3D","5D"):
        vals = by_h.get(label, [])
        horizon_stats.append({"horizon":label, "n":len(vals), "mean_brier":None if not vals else round(sum(vals)/len(vals),6)})

    cal = report.get("belief_calibration") or {}
    hypothesis_brier = dict(cal.get("hypothesis_intelligence") or {})
    overall = cal.get("overall") or {}
    def build_market_view():
        configs = {"BTC/USD": ("btc.", "btc.trend.bullish"), "EUR/USD": ("eurusd.", "eurusd.trend.bullish"), "S&P 500": ("spx.", "spx.trend.bullish")}
        out = []
        for instrument, (prefix, trend_id) in configs.items():
            relevant = [f for f in forecasts if str(f.get("belief_id") or "").startswith(prefix)]
            latest = {}
            for f in sorted(relevant, key=lambda x: str(x.get("forecast_at") or "")):
                bid = str(f.get("belief_id") or "")
                current = latest.get(bid); meta = f.get("metadata") or {}
                if current is None or meta.get("primary_research_horizon") or not (current.get("metadata") or {}).get("primary_research_horizon"):
                    latest[bid] = f
            trend = latest.get(trend_id)
            if not trend: continue
            p = float(trend.get("predicted_probability") or .5)
            direction = "up" if p >= .55 else "down" if p <= .45 else "neutral"
            env_rows = [f for bid, f in latest.items() if bid != trend_id]
            env_ps = [float(f.get("predicted_probability") or .5) for f in env_rows]
            env = sum(env_ps) / len(env_ps) if env_ps else .5
            sentiment = "positive" if env >= .55 else "negative" if env <= .45 else "neutral"
            prior = sorted([f for f in relevant if str(f.get("belief_id")) == trend_id and f is not trend], key=lambda x: str(x.get("forecast_at") or ""), reverse=True)
            previous_p = float(prior[0].get("predicted_probability")) if prior else None
            delta = None if previous_p is None else p - previous_p
            hb = hypothesis_brier.get(trend_id) or {}
            out.append({"instrument":instrument,"trend_belief_id":trend_id,"as_of":trend.get("forecast_at"),"target_at":trend.get("target_at"),
                "horizon_label":horizon_label(trend),"direction":direction,"direction_label":{"up":"WZROSTOWY","down":"SPADKOWY","neutral":"NEUTRALNY"}[direction],
                "trend_probability":round(p,6),"evidence_confidence":trend.get("forecast_confidence"),"sentiment":sentiment,
                "sentiment_label":{"positive":"POZYTYWNE","negative":"NEGATYWNE","neutral":"NEUTRALNE"}[sentiment],"environment_score":round(env,6),
                "environment_components":len(env_rows),"probability_change":None if delta is None else round(delta,6),
                "probability_movement":"stable" if delta is None or abs(delta)<.015 else ("rising" if delta>0 else "falling"),
                "quality":{"n":hb.get("count",hb.get("n")),"mean_brier":hb.get("mean_brier"),"skill_vs_base_rate":hb.get("brier_skill_score_vs_base_rate"),
                           "sample_sufficient":bool(hb.get("sample_sufficient",False))},
                "method":"deterministic_briefrooms_beliefs_v1","llm_authority":False})
        return out

    market_view = build_market_view()
    aris = build_pattern_report(state)

    return {
        "schema_version": "briefrooms_decision_lab_public_v2",
        "model": "BriefRooms Belief Core v2",
        "mode": "research_shadow",
        "execution_authority": False,
        "production_write_authority": False,
        "automatic_tuning": False,
        "generated_at": report.get("generated_at"),
        "market_view": market_view,
        "market_view_contract": {
            "method": "deterministic_briefrooms_beliefs_v1",
            "llm_authority": False,
            "trend_thresholds": {"up": 0.55, "down": 0.45},
            "sentiment_thresholds": {"positive": 0.55, "negative": 0.45},
        },
        "forecasts": rows,
        "multihorizon_paths": multihorizon_paths,
        "horizon_aggregate": horizon_stats,
        "horizon_aggregate_min_sample": 30,
        "hypothesis_brier": hypothesis_brier,
        "hypothesis_intelligence": hypothesis_brier,
        "metrics": {
            "forecast_count": len(forecasts),
            "resolved_count": len(verifications),
            "calibration_eligible": cal.get("count_calibration_eligible", 0),
            "brier_score": overall.get("mean_brier"),
            "ece": overall.get("ece"),
            "log_loss": overall.get("mean_log_loss"),
            "calibration_status": overall.get("status", "awaiting_outcomes"),
        },
        "evidence_patterns": aris.get("patterns", []),
        "evidence_pattern_meta": {
            "schema_version": aris.get("schema_version"),
            "mode": aris.get("mode"),
            "causal_status": aris.get("causal_status"),
            "sample": aris.get("sample", {}),
            "methodology": aris.get("methodology", {}),
            "authority": aris.get("authority", {}),
        },
    }


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--state-dir", required=True)
    p.add_argument("--output", required=True)
    args = p.parse_args()

    root = Path(args.state_dir)
    state = load(root / "state.json", {})
    report = load(root / "BELIEF_CALIBRATION_REPORT.json", {})
    payload = build_payload(state, report)

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
