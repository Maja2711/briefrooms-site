#!/usr/bin/env python3
"""FSE Phase & Cross-Scale Engine.

SHADOW_ONLY extension of IN-09. It keeps the existing deep Fractal Memory
challenger and adds:
- Phase Engine on nested market scales,
- Intrabar Formation tracking from child bars,
- Cross-Scale Alignment,
- Phase Fractal Memory,
- a bounded P Calibration / Regime-Phase Challenger,
- prospective-only evidence for HSE2.

Nothing in this module has production, sizing, stop, policy or execution
authority. Historical analogues initialize hypotheses; only forward frozen
snapshots count as prospective evidence.
"""
from __future__ import annotations

import argparse
import bisect
import hashlib
import json
import math
import statistics
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from scripts import fractal_structure_engine as v1

SCHEMA = "briefrooms-fse-v2-phase-cross-scale-v2"
SNAPSHOT_SCHEMA = "briefrooms-fse-v2-snapshot-v1"  # durable backward-compatible chain
RESOLUTION_SCHEMA = "briefrooms-fse-v2-resolution-v1"
SNAPSHOTS_FILE = "fse_v2_snapshots.jsonl"
RESOLUTIONS_FILE = "fse_v2_resolutions.jsonl"
PUBLIC_SCHEMA = "briefrooms-fse-v2-public-v2"
ZERO_AUTHORITY = dict(v1.ZERO_AUTHORITY)

METHODOLOGY_VERSION = "FSE-PHASE-1.0"
HOURLY_RANGE = "2y"
DAILY_RANGE = "10y"
HORIZON = 4
TOP_K = 80
PHASE_TOP_K = 40
PHASE_PROGRESS_GRID = (0.40, 0.55, 0.70, 0.85)
SCALE_ORDER = ("1m", "5m", "15m", "1h", "4h", "1d", "1w")
NATIVE_FEEDS = {
    "1m": ("7d", "1m"),
    "5m": ("1mo", "5m"),
    "15m": ("1mo", "15m"),
    "1h": (HOURLY_RANGE, "60m"),
    "1d": (DAILY_RANGE, "1d"),
}
P_CALIBRATION_MAX_SHIFT = 0.06


def mean(xs: Sequence[float]) -> float:
    return statistics.fmean(xs) if xs else 0.0


def stdev(xs: Sequence[float]) -> float:
    return statistics.pstdev(xs) if len(xs) >= 2 else 0.0


def clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, float(x)))


def robust_scale(xs: Sequence[float]) -> float:
    if not xs:
        return 1.0
    med = statistics.median(xs)
    mad = statistics.median(abs(x - med) for x in xs)
    return max(1.4826 * mad, 1e-9)


def sample_entropy(xs: Sequence[float], m: int = 2) -> float:
    x = list(xs[-96:])
    if len(x) < 32:
        return 0.0
    r = 0.2 * max(stdev(x), 1e-12)

    def count(k: int) -> int:
        n = 0
        for i in range(len(x) - k):
            for j in range(i + 1, len(x) - k):
                if max(abs(x[i + t] - x[j + t]) for t in range(k)) <= r:
                    n += 1
        return n

    a, b = count(m + 1), count(m)
    return -math.log(max(a, 1) / max(b, 1))


def dfa_alpha(rs: Sequence[float]) -> float:
    x = list(rs[-512:])
    if len(x) < 64:
        return 0.5
    mu = mean(x)
    y, acc = [], 0.0
    for r in x:
        acc += r - mu
        y.append(acc)
    lx, ly = [], []
    for size in (8, 16, 32, 64, 128):
        if size > len(y) // 2:
            continue
        fs = []
        for start in range(0, len(y) - size + 1, size):
            seg = y[start : start + size]
            n = len(seg)
            xx = list(range(n))
            sl = v1.slope(xx, seg)
            if sl is None:
                continue
            intercept = mean(seg) - sl * mean(xx)
            fs.extend((seg[i] - (intercept + sl * i)) ** 2 for i in range(n))
        if fs:
            f = math.sqrt(mean(fs))
            if f > 0:
                lx.append(math.log(size))
                ly.append(math.log(f))
    out = v1.slope(lx, ly)
    return float(out) if out is not None and math.isfinite(out) else 0.5


def haar_scaling(rs: Sequence[float]) -> tuple[float, float]:
    x = list(rs[-512:])
    pts = []
    for scale in (2, 4, 8, 16, 32, 64):
        vals = []
        for i in range(0, len(x) - scale + 1, scale):
            half = scale // 2
            vals.append(abs(mean(x[i : i + half]) - mean(x[i + half : i + scale])))
        if vals and mean(vals) > 0:
            pts.append((math.log(scale), math.log(mean(vals))))
    if len(pts) < 3:
        return 0.0, 0.0
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    sl = v1.slope(xs, ys) or 0.0
    mx, my = mean(xs), mean(ys)
    residual = [y - (my + sl * (x - mx)) for x, y in pts]
    return float(sl), stdev(residual)


Q_GRID = (-4.0, -2.0, -1.0, 1.0, 2.0, 3.0, 4.0)


def generalized_hurst_grid(rs: Sequence[float]) -> dict[float, float]:
    x = list(rs[-512:])
    if len(x) < 64:
        return {q: 0.5 for q in Q_GRID}
    out = {}
    for q in Q_GRID:
        lx, ly = [], []
        for lag in (1, 2, 4, 8, 16, 32):
            vals = [abs(sum(x[i : i + lag])) for i in range(0, len(x) - lag + 1, lag)]
            vals = [max(v, 1e-12) for v in vals]
            if len(vals) < 4:
                continue
            m = mean([v**q for v in vals])
            if m <= 0:
                continue
            m = m ** (1.0 / q)
            lx.append(math.log(lag))
            ly.append(math.log(max(m, 1e-12)))
        h = v1.slope(lx, ly)
        out[q] = float(h) if h is not None and math.isfinite(h) else 0.5
    return out


def box_dimension_path(closes: Sequence[float]) -> float:
    c = list(closes[-256:])
    if len(c) < 32:
        return 1.0
    lo, hi = min(c), max(c)
    span = max(hi - lo, 1e-12)
    y = [(v - lo) / span for v in c]
    logs = []
    for boxes in (4, 8, 16, 32):
        occupied = set()
        for i, val in enumerate(y):
            xi = min(boxes - 1, int(i * boxes / len(y)))
            yi = min(boxes - 1, int(val * boxes))
            occupied.add((xi, yi))
        if len(occupied) > 1:
            logs.append((math.log(boxes), math.log(len(occupied))))
    sl = v1.slope([x for x, _ in logs], [y for _, y in logs]) if len(logs) >= 2 else None
    return float(sl) if sl is not None else 1.0


def shape_vector(bars: Sequence[v1.Bar], points: int = 24) -> list[float]:
    r = list(bars)
    if len(r) < 8:
        return []
    rs = v1.log_returns([b.close for b in r])
    scale = robust_scale(rs)
    acc = [0.0]
    for z in rs:
        acc.append(acc[-1] + z / scale)
    out = []
    for i in range(points):
        p = i * (len(acc) - 1) / (points - 1)
        a, b = int(math.floor(p)), int(math.ceil(p))
        out.append(acc[a] if a == b else acc[a] * (b - p) + acc[b] * (p - a))
    return out


def path_shape(bars: Sequence[v1.Bar], window: int = 96, points: int = 24) -> list[float]:
    return shape_vector(list(bars)[-window:], points)


def geometry_features(bars: Sequence[v1.Bar], window: int = 512) -> list[float]:
    r = list(bars[-window:])
    closes = [b.close for b in r]
    rs = v1.log_returns(closes)
    if len(rs) < 64:
        return []
    hg = generalized_hurst_grid(rs)
    positive = [hg[q] for q in (1.0, 2.0, 3.0, 4.0)]
    width = max(hg.values()) - min(hg.values())
    asym = (hg[-4.0] - hg[-1.0]) - (hg[1.0] - hg[4.0])
    wave_slope, wave_curv = haar_scaling(rs)
    absr = [abs(x) for x in rs]
    med = v1.quantile(absr, 0.5)
    q95 = v1.quantile(absr, 0.95)
    q99 = v1.quantile(absr, 0.99)
    return [
        hg[-4.0], hg[-2.0], hg[-1.0], *positive,
        width, asym, dfa_alpha(rs), wave_slope, wave_curv,
        clamp(max(0.0, v1.excess_kurtosis(rs)) / 12.0),
        clamp((q95 / max(med, 1e-12) - 2.0) / 6.0),
        clamp((q99 / max(q95, 1e-12) - 1.0) / 4.0),
        clamp(sample_entropy(rs) / 3.0),
        clamp(box_dimension_path(closes) - 1.0),
        math.tanh(mean(rs) / max(stdev(rs), 1e-12)),
        clamp(stdev(rs) * 100.0),
    ]


def daily_context(daily: Sequence[v1.Bar], at_ts: datetime) -> list[float]:
    times = [b.timestamp for b in daily]
    idx = bisect.bisect_right(times, at_ts)
    if idx < 128:
        return []
    return geometry_features(daily[:idx], window=min(512, idx))


def vector_distance(a: Sequence[float], b: Sequence[float]) -> float:
    if not a or len(a) != len(b):
        return float("inf")
    return math.sqrt(mean([min((float(x) - float(y)) ** 2, 9.0) for x, y in zip(a, b)]))


def deep_historical_analogues(hourly: Sequence[v1.Bar], daily: Sequence[v1.Bar], top_k: int = TOP_K) -> dict[str, Any]:
    h, d = list(hourly), list(daily)
    current_path = path_shape(h)
    current_geo = geometry_features(h)
    current_daily = daily_context(d, h[-1].timestamp)
    cur = current_path + current_geo + current_daily
    if not current_path or not current_geo or not current_daily:
        return {"source": "DEEP_HISTORY", "analogues_n": 0, "p_up": 0.5, "history_candidates": 0}
    cand = []
    stride = max(4, len(h) // 3500)
    for end in range(512, len(h) - HORIZON - 8, stride):
        hist = h[:end]
        p = path_shape(hist)
        g = geometry_features(hist)
        dc = daily_context(d, h[end - 1].timestamp)
        vec = p + g + dc
        if not p or not g or not dc or len(vec) != len(cur):
            continue
        dist = vector_distance(cur, vec)
        sim = 1 / (1 + dist)
        ref = h[end - 1].close
        fut = h[end : end + HORIZON]
        ret = fut[-1].close / ref - 1
        path = [b.close / ref - 1 for b in fut]
        cand.append({"similarity": sim, "return": ret, "mae": min(path), "mfe": max(path), "at": h[end - 1].timestamp})
    cand.sort(key=lambda x: x["similarity"], reverse=True)
    selected = cand[:top_k]
    if not selected:
        return {"source": "DEEP_HISTORY", "analogues_n": 0, "p_up": 0.5, "history_candidates": 0}
    w = [max(x["similarity"], 0.05) ** 4 for x in selected]
    ws = sum(w)
    pup = sum(wi for wi, x in zip(w, selected) if x["return"] > 0) / ws
    eff = (sum(w) ** 2) / max(sum(x * x for x in w), 1e-12)
    return {
        "source": "DEEP_HISTORY",
        "analogues_n": len(selected),
        "history_candidates": len(cand),
        "p_up": pup,
        "top_similarity": selected[0]["similarity"],
        "mean_similarity": mean([x["similarity"] for x in selected]),
        "effective_analogues": eff,
        "median_forward_return": statistics.median(x["return"] for x in selected),
        "median_adverse_excursion": statistics.median(x["mae"] for x in selected),
        "median_favorable_excursion": statistics.median(x["mfe"] for x in selected),
        "history_start": h[0].timestamp.isoformat().replace("+00:00", "Z"),
        "history_end": h[-1].timestamp.isoformat().replace("+00:00", "Z"),
        "daily_history_start": d[0].timestamp.isoformat().replace("+00:00", "Z") if d else None,
        "daily_history_bars": len(d),
        "hourly_history_bars": len(h),
        "fingerprint_dimensions": len(cur),
        "method": "robust path + generalized Hurst + DFA + Haar + tails + entropy + box dimension + daily context",
    }


def aggregate_bar(group: Sequence[v1.Bar], timestamp: datetime) -> v1.Bar:
    rows = list(group)
    highs = [x.high for x in rows if x.high is not None]
    lows = [x.low for x in rows if x.low is not None]
    return v1.Bar(
        timestamp,
        rows[-1].close,
        rows[0].open if rows[0].open is not None else rows[0].close,
        max(highs) if highs else max(x.close for x in rows),
        min(lows) if lows else min(x.close for x in rows),
        sum(float(x.volume or 0) for x in rows),
    )


def resample_minutes(bars: Sequence[v1.Bar], minutes: int) -> list[v1.Bar]:
    buckets: dict[int, list[v1.Bar]] = {}
    seconds = minutes * 60
    for bar in bars:
        key = int(bar.timestamp.timestamp()) // seconds * seconds
        buckets.setdefault(key, []).append(bar)
    return [aggregate_bar(buckets[k], datetime.fromtimestamp(k, tz=timezone.utc)) for k in sorted(buckets)]


def resample_weekly(bars: Sequence[v1.Bar]) -> list[v1.Bar]:
    buckets: dict[tuple[int, int], list[v1.Bar]] = {}
    for bar in bars:
        iso = bar.timestamp.isocalendar()
        buckets.setdefault((iso.year, iso.week), []).append(bar)
    out = []
    for key in sorted(buckets):
        rows = buckets[key]
        out.append(aggregate_bar(rows, rows[0].timestamp))
    return out


def fetch_multiscale(client: v1.YahooChartClient, symbol: str) -> dict[str, list[v1.Bar]]:
    d = {tf: client.bars(symbol, range_, interval) for tf, (range_, interval) in NATIVE_FEEDS.items()}
    d["4h"] = resample_minutes(d["1h"], 240)
    d["1w"] = resample_weekly(d["1d"])
    return d


def phase_descriptor(bars: Sequence[v1.Bar], window: int = 64) -> dict[str, Any]:
    r = list(bars[-window:])
    if len(r) < 16:
        return {"available": False, "phase": "INSUFFICIENT_DATA", "confidence": 0.0}
    closes = [b.close for b in r]
    rs = v1.log_returns(closes)
    n = len(rs)
    recent_n = max(6, n // 4)
    recent = rs[-recent_n:]
    prior = rs[-2 * recent_n : -recent_n] or rs[:-recent_n]
    net = sum(rs)
    gross = sum(abs(x) for x in rs)
    efficiency = clamp(abs(net) / max(gross, 1e-12))
    sigma = max(stdev(rs), 1e-12)
    trend_z = net / (sigma * math.sqrt(max(n, 1)))
    vol_ratio = stdev(recent) / max(stdev(prior), 1e-12)
    half = max(8, len(closes) // 2)
    early = v1.log_returns(closes[:half])
    late = v1.log_returns(closes[-half:])
    early_mu, late_mu = mean(early), mean(late)
    curvature = math.tanh((late_mu - early_mu) / sigma)
    late_z = sum(recent) / (sigma * math.sqrt(max(len(recent), 1)))
    direction = "UP" if trend_z >= 0.75 else ("DOWN" if trend_z <= -0.75 else "FLAT")
    reversal = direction != "FLAT" and ((direction == "UP" and late_z < -0.45) or (direction == "DOWN" and late_z > 0.45))
    if reversal:
        phase = "REVERSAL"
    elif efficiency >= 0.52 and vol_ratio >= 1.00:
        phase = "EXPANSION"
    elif efficiency >= 0.38 and vol_ratio < 0.82:
        phase = "MATURATION"
    elif efficiency <= 0.24 and vol_ratio <= 1.02:
        phase = "CONSOLIDATION"
    elif vol_ratio >= 1.25:
        phase = "TRANSITION"
    else:
        phase = "DEVELOPMENT"
    confidence = clamp(
        0.40 * min(abs(trend_z) / 2.0, 1.0)
        + 0.30 * min(abs(math.log(max(vol_ratio, 1e-9))) / 0.7, 1.0)
        + 0.30 * abs(curvature)
    )
    signature = {
        "direction": direction,
        "phase": phase,
        "efficiency_bucket": int(round(efficiency * 10)),
        "vol_bucket": int(round(clamp(vol_ratio / 2.0) * 10)),
        "curvature_bucket": int(round((curvature + 1.0) * 5)),
    }
    sid = "FS-" + hashlib.sha256(v1.canonical(signature).encode()).hexdigest()[:8].upper()
    return {
        "available": True,
        "structure_id": sid,
        "phase": phase,
        "direction": direction,
        "confidence": confidence,
        "efficiency": efficiency,
        "trend_z": trend_z,
        "volatility_ratio": vol_ratio,
        "curvature": curvature,
    }


def phase_analogues(
    bars: Sequence[v1.Bar],
    current_window: int = 48,
    motif_window: int = 96,
    top_k: int = PHASE_TOP_K,
    max_history: int = 4000,
) -> dict[str, Any]:
    all_rows = list(bars)
    r = all_rows[-max_history:]
    if len(r) < max(motif_window * 3, 180):
        return {"source": "PHASE_MEMORY", "analogues_n": 0, "p_up_remaining": 0.5, "phase_progress": None}
    cur = shape_vector(r[-current_window:])
    if not cur:
        return {"source": "PHASE_MEMORY", "analogues_n": 0, "p_up_remaining": 0.5, "phase_progress": None}
    candidates = []
    last_start = len(r) - current_window - motif_window
    stride = max(4, len(r) // 700)
    for start in range(0, max(0, last_start), stride):
        motif = r[start : start + motif_window]
        best = None
        for progress in PHASE_PROGRESS_GRID:
            plen = max(12, int(round(motif_window * progress)))
            prefix = motif[:plen]
            fp = shape_vector(prefix)
            if not fp:
                continue
            sim = 1.0 / (1.0 + vector_distance(cur, fp))
            if best is None or sim > best["similarity"]:
                ref = prefix[-1].close
                remaining = motif[plen:]
                if not remaining or ref <= 0:
                    continue
                ret = remaining[-1].close / ref - 1.0
                best = {
                    "similarity": sim,
                    "progress": progress,
                    "return": ret,
                    "remaining_bars": len(remaining),
                }
        if best is not None:
            candidates.append(best)
    candidates.sort(key=lambda x: x["similarity"], reverse=True)
    selected = candidates[:top_k]
    if not selected:
        return {"source": "PHASE_MEMORY", "analogues_n": 0, "p_up_remaining": 0.5, "phase_progress": None}
    weights = [max(x["similarity"], 0.05) ** 4 for x in selected]
    ws = sum(weights)
    p_up = sum(w for w, x in zip(weights, selected) if x["return"] > 0) / ws
    progress = sum(w * x["progress"] for w, x in zip(weights, selected)) / ws
    return {
        "source": "PHASE_MEMORY",
        "analogues_n": len(selected),
        "history_candidates": len(candidates),
        "p_up_remaining": p_up,
        "phase_progress": progress,
        "top_similarity": selected[0]["similarity"],
        "mean_similarity": mean([x["similarity"] for x in selected]),
        "median_remaining_return": statistics.median(x["return"] for x in selected),
        "median_remaining_bars": statistics.median(x["remaining_bars"] for x in selected),
        "progress_grid": list(PHASE_PROGRESS_GRID),
        "method": "current partial path matched to historical motif prefixes at multiple completion stages",
    }


def expected_child_count(parent: Sequence[v1.Bar], child: Sequence[v1.Bar]) -> int | None:
    p, c = list(parent), list(child)
    if len(p) < 3 or not c:
        return None
    counts = []
    starts = [x.timestamp for x in p[-30:]]
    for a, b in zip(starts, starts[1:]):
        n = sum(1 for x in c if a <= x.timestamp < b)
        if n > 0:
            counts.append(n)
    return int(round(statistics.median(counts))) if counts else None


def intrabar_formation(parent: Sequence[v1.Bar], child: Sequence[v1.Bar], parent_scale: str, child_scale: str) -> dict[str, Any]:
    p, c = list(parent), list(child)
    if not p or not c:
        return {"available": False, "parent_scale": parent_scale, "child_scale": child_scale}
    start = p[-1].timestamp
    rows = [x for x in c if x.timestamp >= start]
    expected = expected_child_count(p, c)
    if not rows or not expected:
        return {"available": False, "parent_scale": parent_scale, "child_scale": child_scale}
    closes = [x.close for x in rows]
    rs = v1.log_returns(closes)
    gross = sum(abs(x) for x in rs)
    net = sum(rs)
    efficiency = abs(net) / max(gross, 1e-12) if rs else 0.0
    highs = [float(x.high if x.high is not None else x.close) for x in rows]
    lows = [float(x.low if x.low is not None else x.close) for x in rows]
    lo, hi = min(lows), max(highs)
    internal = (closes[-1] - lo) / max(hi - lo, 1e-12)
    direction = "UP" if net > 0 else ("DOWN" if net < 0 else "FLAT")
    return {
        "available": True,
        "parent_scale": parent_scale,
        "child_scale": child_scale,
        "parent_started_at": start.isoformat().replace("+00:00", "Z"),
        "observed_child_bars": len(rows),
        "expected_child_bars": expected,
        "formation_progress": clamp(len(rows) / max(expected, 1)),
        "direction": direction,
        "efficiency": clamp(efficiency),
        "internal_position": clamp(internal),
        "range_fraction": (hi - lo) / max(closes[0], 1e-12),
        "is_partial": len(rows) < expected,
    }


def build_phase_map(scales: Mapping[str, Sequence[v1.Bar]]) -> dict[str, Any]:
    out = {}
    for tf in SCALE_ORDER:
        bars = list(scales.get(tf) or [])
        descriptor = phase_descriptor(bars)
        memory = phase_analogues(bars)
        out[tf] = {
            **descriptor,
            "bars": len(bars),
            "observed_at": bars[-1].timestamp.isoformat().replace("+00:00", "Z") if bars else None,
            "phase_progress": memory.get("phase_progress"),
            "phase_memory": memory,
        }
    return out


def cross_scale_alignment(phase_map: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    pairs = []
    for fast, slow in zip(SCALE_ORDER, SCALE_ORDER[1:]):
        a, b = phase_map.get(fast) or {}, phase_map.get(slow) or {}
        if not a.get("available") or not b.get("available"):
            continue
        da, db = a.get("direction"), b.get("direction")
        direction_score = 0.5 if "FLAT" in {da, db} else (1.0 if da == db else 0.0)
        shape_dist = math.sqrt(mean([
            (float(a.get("efficiency") or 0) - float(b.get("efficiency") or 0)) ** 2,
            (math.tanh(math.log(max(float(a.get("volatility_ratio") or 1), 1e-9))) - math.tanh(math.log(max(float(b.get("volatility_ratio") or 1), 1e-9)))) ** 2,
            (float(a.get("curvature") or 0) - float(b.get("curvature") or 0)) ** 2,
        ]))
        shape_score = clamp(1.0 - shape_dist / 1.25)
        conf = math.sqrt(clamp(float(a.get("confidence") or 0)) * clamp(float(b.get("confidence") or 0)))
        pa, pb = a.get("phase_progress"), b.get("phase_progress")
        lead = None if pa is None or pb is None else clamp(0.5 + (float(pa) - float(pb)) / 0.8)
        score = 0.45 * direction_score + 0.40 * shape_score + 0.15 * conf
        pairs.append({"fast": fast, "slow": slow, "alignment": score, "fast_lead": lead})
    alignment = mean([x["alignment"] for x in pairs]) if pairs else 0.0
    leads = [float(x["fast_lead"]) for x in pairs if x["fast_lead"] is not None]
    lead_score = mean(leads) if leads else None
    ranked = []
    for tf, row in phase_map.items():
        mem = row.get("phase_memory") if isinstance(row.get("phase_memory"), Mapping) else {}
        quality = float(row.get("confidence") or 0) * float(mem.get("top_similarity") or 0)
        ranked.append((quality, tf))
    dominant_scale = max(ranked)[1] if ranked else None
    state = "COHERENT" if alignment >= 0.70 else ("MIXED" if alignment >= 0.50 else "FRACTURED")
    return {
        "alignment_score": alignment,
        "cascade_state": state,
        "fast_scale_lead_score": lead_score,
        "dominant_scale": dominant_scale,
        "pairs": pairs,
    }


def calibration_challenger(base_p: float, phase_p: float, alignment: float, regime: str, phase_available: bool) -> dict[str, Any]:
    base = clamp(base_p, 0.01, 0.99)
    phase = clamp(phase_p, 0.01, 0.99)
    if not phase_available:
        return {
            "active": False,
            "p_base": base,
            "p_challenger": base,
            "phase_p": phase,
            "shift_pp": 0.0,
            "reason": "insufficient phase-memory evidence",
        }
    weight = 0.10 + 0.25 * clamp(alignment)
    blend = (1.0 - weight) * base + weight * phase
    shrink = {"TURBULENT": 0.85, "TRANSITION": 0.90}.get(str(regime), 1.0)
    raw = 0.5 + (blend - 0.5) * shrink
    delta = max(-P_CALIBRATION_MAX_SHIFT, min(P_CALIBRATION_MAX_SHIFT, raw - base))
    challenger = clamp(base + delta, 0.01, 0.99)
    return {
        "active": True,
        "p_base": base,
        "p_challenger": challenger,
        "phase_p": phase,
        "shift_pp": delta * 100.0,
        "max_abs_shift_pp": P_CALIBRATION_MAX_SHIFT * 100.0,
        "phase_weight": weight,
        "regime_shrink": shrink,
        "method": "bounded blend of FSE deep-memory P with 1h Phase Memory, confidence governed by cross-scale alignment and regime",
        "production_applied": False,
    }


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return v1.read_jsonl(path)


def append_chain(path: Path, schema: str, payload: Mapping[str, Any], id_key: str) -> dict[str, Any]:
    return v1.append_chain(path, schema, payload, id_key)


def build_instrument_state(instrument: str, symbol: str, scales: Mapping[str, Sequence[v1.Bar]]) -> dict[str, Any]:
    hourly, daily = list(scales["1h"]), list(scales["1d"])
    deep = deep_historical_analogues(hourly, daily)
    phase_map = build_phase_map(scales)
    alignment = cross_scale_alignment(phase_map)
    formation = {}
    for parent, child in (("5m", "1m"), ("15m", "5m"), ("1h", "15m"), ("4h", "1h"), ("1d", "1h"), ("1w", "1d")):
        formation[parent] = intrabar_formation(scales[parent], scales[child], parent, child)
    feature_scales = {
        tf: v1.scale_features(scales[tf], {"5m": 5, "15m": 15, "1h": 60, "4h": 240, "1d": 1440}[tf])
        for tf in ("5m", "15m", "1h", "4h", "1d")
    }
    risk = v1.structural_risk(feature_scales)
    base_p = clamp(float(deep.get("p_up", 0.5)))
    phase_mem = (phase_map.get("1h") or {}).get("phase_memory") or {}
    phase_p = clamp(float(phase_mem.get("p_up_remaining", 0.5)))
    phase_available = int(phase_mem.get("analogues_n") or 0) >= 12
    challenger = calibration_challenger(base_p, phase_p, float(alignment.get("alignment_score") or 0), str(risk.get("regime")), phase_available)
    return {
        "instrument": instrument,
        "symbol": symbol,
        "observed_at": hourly[-1].timestamp.isoformat().replace("+00:00", "Z"),
        "reference_price": hourly[-1].close,
        "forecast": "UP" if base_p >= 0.55 else ("DOWN" if base_p <= 0.45 else "NEUTRAL"),
        "memory": deep,
        "phase_map": phase_map,
        "intrabar_formation": formation,
        "cross_scale_alignment": alignment,
        "structural_risk": risk,
        "p_calibration_challenger": challenger,
    }


def resolve(state_dir: Path, hourly_by: Mapping[str, Sequence[v1.Bar]]) -> None:
    done = {str(x.get("snapshot_id")) for x in read_jsonl(state_dir / RESOLUTIONS_FILE)}
    for s in read_jsonl(state_dir / SNAPSHOTS_FILE):
        sid = str(s.get("snapshot_id") or "")
        if not sid or sid in done:
            continue
        future = [b for b in hourly_by.get(str(s.get("instrument")), []) if b.timestamp > v1.parse_time(str(s["observed_at"]))]
        if len(future) < HORIZON:
            continue
        path = future[:HORIZON]
        ref = float(s["reference_price"])
        ret = path[-1].close / ref - 1
        y = 1 if ret > 0 else 0
        base_p = clamp(float(s.get("base_p_up", s.get("p_up_4h", 0.5))))
        base_brier = (base_p - y) ** 2
        phase_p = v1.finite(s.get("phase_p_up"))
        challenger_p = v1.finite(s.get("challenger_p_up"))
        phase_brier = None if phase_p is None else (clamp(phase_p) - y) ** 2
        challenger_brier = None if challenger_p is None else (clamp(challenger_p) - y) ** 2
        rid = "fsev2res-" + v1.sha({"snapshot_id": sid, "resolved_at": path[-1].timestamp.isoformat()})[:24]
        payload = {
            "resolution_id": rid,
            "snapshot_id": sid,
            "instrument": s["instrument"],
            "observed_at": s["observed_at"],
            "resolved_at": path[-1].timestamp.isoformat().replace("+00:00", "Z"),
            "horizon": "4x1h_market_bars",
            "forward_return": ret,
            "outcome_up": bool(y),
            "directional_brier": base_brier,
            "edge_vs_0_5": 0.25 - base_brier,
            "phase_brier": phase_brier,
            "phase_edge_vs_0_5": None if phase_brier is None else 0.25 - phase_brier,
            "challenger_brier": challenger_brier,
            "calibration_brier_improvement_vs_base": None if challenger_brier is None else base_brier - challenger_brier,
            "mae_fraction": min(b.close / ref - 1 for b in path),
            "mfe_fraction": max(b.close / ref - 1 for b in path),
            "methodology_version": s.get("methodology_version"),
            "prospective_only": True,
            "authority": dict(ZERO_AUTHORITY),
        }
        append_chain(state_dir / RESOLUTIONS_FILE, RESOLUTION_SCHEMA, payload, "resolution_id")
        done.add(sid)


def snapshot(state_dir: Path, row: Mapping[str, Any]) -> None:
    prior = [x for x in read_jsonl(state_dir / SNAPSHOTS_FILE) if x.get("instrument") == row["instrument"]]
    if prior and prior[-1].get("observed_at") == row["observed_at"]:
        return
    sid = "fsev2snap-" + v1.sha({"instrument": row["instrument"], "observed_at": row["observed_at"]})[:24]
    phase_1h = row["phase_map"].get("1h") or {}
    phase_mem = phase_1h.get("phase_memory") or {}
    challenger = row["p_calibration_challenger"]
    append_chain(
        state_dir / SNAPSHOTS_FILE,
        SNAPSHOT_SCHEMA,
        {
            "snapshot_id": sid,
            "instrument": row["instrument"],
            "symbol": row["symbol"],
            "observed_at": row["observed_at"],
            "reference_price": row["reference_price"],
            "p_up_4h": row["memory"]["p_up"],
            "base_p_up": row["memory"]["p_up"],
            "phase_p_up": phase_mem.get("p_up_remaining"),
            "challenger_p_up": challenger.get("p_challenger") if challenger.get("active") else None,
            "forecast": row["forecast"],
            "analogue_n": row["memory"]["analogues_n"],
            "history_candidates": row["memory"]["history_candidates"],
            "fingerprint_dimensions": row["memory"]["fingerprint_dimensions"],
            "phase_structure_id": phase_1h.get("structure_id"),
            "phase_label": phase_1h.get("phase"),
            "phase_progress": phase_mem.get("phase_progress"),
            "phase_similarity": phase_mem.get("top_similarity"),
            "cross_scale_alignment": row["cross_scale_alignment"].get("alignment_score"),
            "cascade_state": row["cross_scale_alignment"].get("cascade_state"),
            "regime": row["structural_risk"].get("regime"),
            "methodology_version": METHODOLOGY_VERSION,
            "prospective_only": True,
            "authority": dict(ZERO_AUTHORITY),
        },
        "snapshot_id",
    )


def verify(state_dir: Path) -> dict[str, Any]:
    a = v1.verify_chain(state_dir / SNAPSHOTS_FILE, SNAPSHOT_SCHEMA, "snapshot_id")
    b = v1.verify_chain(state_dir / RESOLUTIONS_FILE, RESOLUTION_SCHEMA, "resolution_id")
    for row in read_jsonl(state_dir / SNAPSHOTS_FILE) + read_jsonl(state_dir / RESOLUTIONS_FILE):
        if row.get("authority") != ZERO_AUTHORITY:
            raise ValueError("FSE v2 authority violation")
    return {"ok": True, "snapshots": a, "resolutions": b, "zero_authority": True}


def metric(resolutions: Sequence[Mapping[str, Any]], instrument: str) -> dict[str, Any]:
    rows = [x for x in resolutions if x.get("instrument") == instrument]
    edges = [float(x["edge_vs_0_5"]) for x in rows if v1.finite(x.get("edge_vs_0_5")) is not None]
    return {
        "instrument": instrument,
        "counter": len(edges),
        "target_n": 40,
        "total_edge": sum(edges),
        "mean_edge": mean(edges) if edges else None,
        "mean_brier": (0.25 - mean(edges)) if edges else None,
        "baseline_brier": 0.25,
        "status": "RUNNING_SHADOW" if len(edges) < 40 else "READY_FOR_REVIEW",
    }


def hse_measurements(resolutions: Sequence[Mapping[str, Any]], instrument: str) -> list[dict[str, Any]]:
    rows = [x for x in resolutions if x.get("instrument") == instrument and x.get("methodology_version") == METHODOLOGY_VERSION]
    phase = [float(x["phase_edge_vs_0_5"]) for x in rows if v1.finite(x.get("phase_edge_vs_0_5")) is not None]
    calibration = [
        float(x["calibration_brier_improvement_vs_base"])
        for x in rows
        if v1.finite(x.get("calibration_brier_improvement_vs_base")) is not None
    ]
    return [
        {
            "proposal_key": f"fse-phase-memory-{instrument.lower()}-4h-brier",
            "claim": f"FSE Phase Fractal Memory for {instrument} improves prospective 4x1h directional Brier versus 50/50.",
            "champion": "50/50 directional baseline",
            "challenger": "FSE Phase Fractal Memory",
            "metric_name": "phase_brier_improvement_vs_0_5",
            "target_n": 40,
            "success_mean_edge": 0.0025,
            "reject_mean_edge": -0.0025,
            "counter": len(phase),
            "total": sum(phase),
            "details": {
                "instrument": instrument,
                "horizon": "4x1h_market_bars",
                "kind": "phase_memory",
                "methodology_version": METHODOLOGY_VERSION,
            },
        },
        {
            "proposal_key": f"fse-regime-phase-calibration-{instrument.lower()}-4h-brier",
            "claim": f"FSE Regime-Phase P Calibration Challenger for {instrument} improves prospective Brier versus the frozen FSE v2 base probability.",
            "champion": "FSE v2 Deep Fractal Memory P",
            "challenger": "FSE Regime-Phase P Calibration Challenger",
            "metric_name": "calibration_brier_improvement_vs_fse_v2_base",
            "target_n": 40,
            "success_mean_edge": 0.0025,
            "reject_mean_edge": -0.0025,
            "counter": len(calibration),
            "total": sum(calibration),
            "details": {
                "instrument": instrument,
                "horizon": "4x1h_market_bars",
                "kind": "p_calibration_regime_phase",
                "max_abs_shift_pp": P_CALIBRATION_MAX_SHIFT * 100.0,
                "methodology_version": METHODOLOGY_VERSION,
            },
        },
    ]


def promotion_gate(measurements: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    ready = []
    for row in measurements:
        n = int(row.get("counter") or 0)
        total = float(row.get("total") or 0)
        edge = total / n if n else None
        ready.append(n >= int(row.get("target_n") or 40) and edge is not None and edge >= float(row.get("success_mean_edge") or 0.0025))
    return {
        "status": "READY_FOR_MANUAL_REVIEW" if ready and all(ready) else "NOT_ELIGIBLE",
        "automatic_promotion": False,
        "requires_hse2_supported": True,
        "requires_explicit_per_engine_bridge": True,
        "production_writeback": False,
    }


def run_cycle(
    root: Path,
    state_dir: Path,
    public: Path,
    instruments: Mapping[str, str] | None = None,
    client: v1.YahooChartClient | None = None,
    at: str | None = None,
) -> dict[str, Any]:
    instruments = dict(instruments or v1.DEFAULT_INSTRUMENTS)
    client = client or v1.YahooChartClient()
    state_dir.mkdir(parents=True, exist_ok=True)
    raw_hourly: dict[str, list[v1.Bar]] = {}
    states, errors = [], {}
    for iid, symbol in instruments.items():
        try:
            scales = fetch_multiscale(client, symbol)
            raw_hourly[iid] = scales["1h"]
            states.append(build_instrument_state(iid, symbol, scales))
        except Exception as exc:
            errors[iid] = f"{type(exc).__name__}: {exc}"
    if not states:
        raise RuntimeError("FSE v2 has no usable instruments: " + v1.canonical(errors))

    resolve(state_dir, raw_hourly)
    for row in states:
        snapshot(state_dir, row)
    check = verify(state_dir)
    resolutions = read_jsonl(state_dir / RESOLUTIONS_FILE)
    generated = at or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    legacy = [metric(resolutions, i) for i in instruments]
    hse = []
    for i in instruments:
        hse.extend(hse_measurements(resolutions, i))

    public_rows = []
    for row in states:
        public_rows.append({
            "instrument": row["instrument"],
            "symbol": row["symbol"],
            "observed_at": row["observed_at"],
            "reference_price": row["reference_price"],
            "forecast": row["forecast"],
            "memory": row["memory"],
            "regime": row["structural_risk"]["regime"],
            "risk_score": row["structural_risk"]["risk_score"],
            "phase_map": row["phase_map"],
            "intrabar_formation": row["intrabar_formation"],
            "cross_scale_alignment": row["cross_scale_alignment"],
            "p_calibration_challenger": row["p_calibration_challenger"],
        })

    out = {
        "schema_version": PUBLIC_SCHEMA,
        "engine": "FSE v2 — Phase & Cross-Scale Engine",
        "module_id": "IN-09",
        "component_id": "FSE-PHASE",
        "mode": "SHADOW_ONLY",
        "production_impact": False,
        "authority": dict(ZERO_AUTHORITY),
        "generated_at": generated,
        "methodology_version": METHODOLOGY_VERSION,
        "methodology_frozen": True,
        "pipeline": "FSE -> PHASE ENGINE -> INTRABAR FORMATION -> CROSS-SCALE ALIGNMENT -> PHASE FRACTAL MEMORY -> PROSPECTIVE HSE2 -> P CALIBRATION CHALLENGER -> MANUAL PROMOTION GATE",
        "source_policy": {
            "native_scales": {k: {"range": v[0], "interval": v[1]} for k, v in NATIVE_FEEDS.items()},
            "derived_scales": {"4h": "calendar-aligned 1h aggregation", "1w": "ISO-week aggregation from 1d"},
            "deep_memory_hourly_range": HOURLY_RANGE,
            "daily_context_range": DAILY_RANGE,
            "phase_history_cap_bars": 4000,
        },
        "instruments": public_rows,
        "measurements": legacy,
        "hse_measurements": hse,
        "promotion_gate": promotion_gate(hse),
        "state_verification": check,
        "errors": errors,
        "notes": [
            "Phase progress is estimated from historical motif-prefix matches, not asserted as a deterministic law.",
            "Intrabar Formation reads lower-timeframe bars inside the currently forming higher-timeframe bar.",
            "Cross-scale alignment is descriptive research evidence; it does not itself create a trade.",
            "P Calibration Challenger is capped at +/-6 percentage points and remains shadow-only.",
            "No historical phase/calibration record is credited prospectively; only snapshots frozen under FSE-PHASE-1.0 count.",
        ],
    }
    public.parent.mkdir(parents=True, exist_ok=True)
    public.write_text(json.dumps(out, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=".")
    ap.add_argument("--state-dir", required=True)
    ap.add_argument("--public", default="data/investments/fse_v2_public.json")
    ap.add_argument("--verify", action="store_true")
    args = ap.parse_args()
    state = Path(args.state_dir)
    if args.verify:
        print(json.dumps(verify(state), indent=2))
        return 0
    out = run_cycle(Path(args.root), state, Path(args.public))
    print(json.dumps({
        "engine": out["engine"],
        "methodology_version": out["methodology_version"],
        "instruments": len(out["instruments"]),
        "hse_measurements": len(out["hse_measurements"]),
        "promotion_gate": out["promotion_gate"]["status"],
        "production_impact": out["production_impact"],
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
