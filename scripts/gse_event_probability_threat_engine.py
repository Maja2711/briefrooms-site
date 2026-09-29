#!/usr/bin/env python3
"""GSE Event Probability / Threat Engine.

Shadow-only early-warning layer for geopolitical event occurrence probability.

Pipeline:
GSE evidence -> precursor features -> event probability -> frozen forecast
-> point-in-time verification -> Brier/log-loss calibration.

The initial probability mapping is an explicitly uncalibrated engineering seed.
It must not be interpreted as an authoritative intelligence estimate. The purpose
of the prospective ledger is to measure whether the signal layer improves on its
stored prior before any later human-reviewed model promotion.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import mean
from typing import Any, Dict, Iterable, List, Mapping, Sequence, Tuple

UTC = timezone.utc
MODE = "shadow"
SCHEMA_VERSION = "gse-event-threat-v1"
FORECAST_HORIZONS_H = (168, 720, 2160)  # 7d / 30d / 90d
DAILY_FREEZE_HOUR_UTC = 0
MIN_SECONDARY_CONFIRMATIONS = 2

BASE_PRIOR_BY_HORIZON: Mapping[int, float] = {
    168: 0.005,
    720: 0.012,
    2160: 0.025,
}

# These are engineering seed priors, not empirical attack base rates.
# The aggregate NATO-east-flank event receives a wider prior because it is a
# logical union of several country-level events.
EVENT_DEFINITIONS: Mapping[str, Mapping[str, Any]] = {
    "russia_nato_attack_any_eastern_flank": {
        "label": "Russia armed attack on NATO eastern flank",
        "target": "NATO eastern flank",
        "target_terms": (
            "poland", "polish", "lithuania", "lithuanian", "latvia", "latvian",
            "estonia", "estonian", "baltic states", "baltics", "suwalki",
        ),
        "prior_multiplier": 1.8,
        "cap": 0.45,
    },
    "russia_attack_poland": {
        "label": "Russia armed attack on Poland",
        "target": "Poland",
        "target_terms": ("poland", "polish", "suwalki"),
        "prior_multiplier": 1.0,
        "cap": 0.35,
    },
    "russia_attack_lithuania": {
        "label": "Russia armed attack on Lithuania",
        "target": "Lithuania",
        "target_terms": ("lithuania", "lithuanian", "suwalki"),
        "prior_multiplier": 1.0,
        "cap": 0.35,
    },
    "russia_attack_latvia": {
        "label": "Russia armed attack on Latvia",
        "target": "Latvia",
        "target_terms": ("latvia", "latvian"),
        "prior_multiplier": 1.0,
        "cap": 0.35,
    },
    "russia_attack_estonia": {
        "label": "Russia armed attack on Estonia",
        "target": "Estonia",
        "target_terms": ("estonia", "estonian"),
        "prior_multiplier": 1.0,
        "cap": 0.35,
    },
}

ACTOR_TERMS = ("russia", "russian", "moscow", "kremlin")
REGIONAL_TERMS = (
    "nato", "eastern flank", "baltic", "baltics", "poland", "lithuania",
    "latvia", "estonia", "suwalki", "kaliningrad", "belarus",
)

PRECURSOR_CATEGORIES: Mapping[str, Tuple[str, ...]] = {
    "force_posture": (
        "troop buildup", "troop build-up", "military buildup", "military build-up",
        "deployment", "deploys", "reinforcement", "reinforces", "forward deploy",
        "combat ready", "readiness", "exercise", "drill", "zapad",
    ),
    "mobilization_logistics": (
        "mobilization", "mobilisation", "reserve call-up", "logistics", "ammunition",
        "field hospital", "rail movement", "military convoy", "fuel depot",
    ),
    "border_airspace_incident": (
        "border incident", "border violation", "airspace violation", "airspace incursion",
        "drone incursion", "missile crossed", "border crossing", "intercepted",
    ),
    "hybrid_cyber": (
        "cyberattack", "cyber attack", "sabotage", "gps jamming", "jamming",
        "hybrid attack", "hybrid operation", "critical infrastructure", "undersea cable",
    ),
    "coercive_rhetoric": (
        "ultimatum", "threatens", "threatened", "retaliation", "red line",
        "military response", "nuclear threat", "warned nato",
    ),
    "alliance_response": (
        "article 4", "article 5", "nato reinforcement", "allied reinforcement",
        "air policing", "forward presence", "rapid reaction", "defence plans",
        "defense plans",
    ),
}

REALIZATION_TERMS = (
    "attacks poland", "attacked poland", "invades poland", "invaded poland",
    "attacks lithuania", "attacked lithuania", "invades lithuania", "invaded lithuania",
    "attacks latvia", "attacked latvia", "invades latvia", "invaded latvia",
    "attacks estonia", "attacked estonia", "invades estonia", "invaded estonia",
    "armed attack", "armed incursion", "crossed the border", "crosses the border",
    "missile strike on", "missile strikes on", "air strike on", "air strikes on",
    "ground forces entered", "troops entered",
)

HYPOTHETICAL_MARKERS = (
    "could attack", "might attack", "may attack", "would attack", "could invade",
    "might invade", "may invade", "would invade", "risk of attack", "risk of invasion",
    "scenario", "war game", "simulation", "exercise simulates", "threatens to attack",
    "warning that russia", "warns that russia",
)


def clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, float(value)))


def iso_z(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def parse_time(value: str | datetime) -> datetime:
    if isinstance(value, datetime):
        dt = value
    else:
        text = str(value).strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        dt = datetime.fromisoformat(text)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def stable_id(prefix: str, *parts: Any) -> str:
    raw = "|".join(str(x) for x in parts)
    return f"{prefix}-{hashlib.sha256(raw.encode('utf-8')).hexdigest()[:20]}"


def log_loss(probability: float, outcome: bool) -> float:
    p = clamp(probability, 1e-9, 1.0 - 1e-9)
    y = 1.0 if outcome else 0.0
    return -(y * math.log(p) + (1.0 - y) * math.log(1.0 - p))


def _read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)


def _read_jsonl(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    out: List[Dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            out.append(row)
    return out


def _append_unique(path: Path, rows: Iterable[Mapping[str, Any]], key: str) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = {str(row.get(key)) for row in _read_jsonl(path)}
    new_rows: List[Mapping[str, Any]] = []
    for row in rows:
        value = str(row.get(key))
        if not value or value in existing:
            continue
        existing.add(value)
        new_rows.append(row)
    if not new_rows:
        return 0
    with path.open("a", encoding="utf-8") as handle:
        for row in new_rows:
            handle.write(json.dumps(dict(row), ensure_ascii=False, sort_keys=True) + "\n")
    return len(new_rows)


def _normalize(text: Any) -> str:
    return " ".join(str(text or "").lower().split())


def _contains_any(text: str, terms: Sequence[str]) -> bool:
    return any(term in text for term in terms)


@dataclass(frozen=True)
class ThreatEstimate:
    event_type: str
    label: str
    target: str
    generated_at: str
    horizon_hours: int
    prior_probability: float
    predicted_probability: float
    confidence: float
    signal_score: float
    evidence_24h: int
    evidence_7d: int
    evidence_30d: int
    independent_sources_7d: int
    primary_sources_30d: int
    precursor_categories: Tuple[str, ...]
    supporting_evidence_ids: Tuple[str, ...]
    calibration_status: str = "uncalibrated_seed"

    def to_dict(self) -> Dict[str, Any]:
        payload = asdict(self)
        payload["precursor_categories"] = list(self.precursor_categories)
        payload["supporting_evidence_ids"] = list(self.supporting_evidence_ids)
        return payload


@dataclass(frozen=True)
class EventForecast:
    forecast_id: str
    batch_id: str
    event_type: str
    label: str
    target: str
    forecast_at: str
    target_at: str
    horizon_hours: int
    prior_probability: float
    predicted_probability: float
    confidence: float
    signal_score: float
    precursor_categories: Tuple[str, ...]
    supporting_evidence_ids: Tuple[str, ...]
    probability_semantics: str = "research_event_probability_uncalibrated"
    mode: str = MODE

    def to_dict(self) -> Dict[str, Any]:
        payload = asdict(self)
        payload["precursor_categories"] = list(self.precursor_categories)
        payload["supporting_evidence_ids"] = list(self.supporting_evidence_ids)
        return payload


@dataclass(frozen=True)
class EventVerification:
    verification_id: str
    forecast_id: str
    event_type: str
    horizon_hours: int
    predicted_probability: float
    prior_probability: float
    outcome: bool
    verified_at: str
    realization_evidence_ids: Tuple[str, ...]
    confirmation_sources: int
    calibration_eligible: bool
    coverage_cycles: int
    coverage_days: int
    brier_score: float
    prior_brier_score: float
    log_loss: float

    def to_dict(self) -> Dict[str, Any]:
        payload = asdict(self)
        payload["realization_evidence_ids"] = list(self.realization_evidence_ids)
        return payload


class EventThreatStore:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self.evidence_path = root / "gse_evidence.jsonl"
        self.state_path = root / "gse_event_threat_state.json"
        self.forecasts_path = root / "gse_event_probability_forecasts.jsonl"
        self.verifications_path = root / "gse_event_probability_verifications.jsonl"
        self.calibration_path = root / "gse_event_probability_calibration.json"
        self.cycles_path = root / "gse_event_threat_cycles.jsonl"

    def evidence(self) -> List[Dict[str, Any]]:
        return _read_jsonl(self.evidence_path)

    def forecasts(self) -> List[Dict[str, Any]]:
        return _read_jsonl(self.forecasts_path)

    def verifications(self) -> List[Dict[str, Any]]:
        return _read_jsonl(self.verifications_path)

    def cycles(self) -> List[Dict[str, Any]]:
        return _read_jsonl(self.cycles_path)


class EventProbabilityThreatEngine:
    def __init__(self, root: Path) -> None:
        self.store = EventThreatStore(root)

    @staticmethod
    def _event_prior(event_type: str, horizon_hours: int) -> float:
        definition = EVENT_DEFINITIONS[event_type]
        base = float(BASE_PRIOR_BY_HORIZON[horizon_hours])
        return clamp(base * float(definition.get("prior_multiplier") or 1.0), 0.001, 0.10)

    @staticmethod
    def _precursor_categories(text: str) -> Tuple[str, ...]:
        return tuple(name for name, terms in PRECURSOR_CATEGORIES.items() if _contains_any(text, terms))

    @staticmethod
    def _event_relevant(event_type: str, row: Mapping[str, Any]) -> Tuple[bool, Tuple[str, ...]]:
        text = _normalize(f"{row.get('title','')} {row.get('text','')}")
        definition = EVENT_DEFINITIONS[event_type]
        if not _contains_any(text, ACTOR_TERMS):
            return False, ()
        target_terms = tuple(definition["target_terms"])
        target_match = _contains_any(text, target_terms)
        regional_match = _contains_any(text, REGIONAL_TERMS)
        if event_type != "russia_nato_attack_any_eastern_flank" and not target_match:
            return False, ()
        if event_type == "russia_nato_attack_any_eastern_flank" and not regional_match:
            return False, ()
        categories = EventProbabilityThreatEngine._precursor_categories(text)
        if not categories:
            # Direct attack language itself is also a maximum-severity precursor.
            if _contains_any(text, REALIZATION_TERMS):
                categories = ("direct_attack_language",)
            else:
                return False, ()
        return True, categories

    @staticmethod
    def _row_weight(row: Mapping[str, Any], now: datetime, categories: Sequence[str]) -> float:
        reliability = clamp(float(row.get("reliability") or 0.5), 0.1, 1.0)
        age_h = max(0.0, (now - parse_time(str(row["published_at"]))).total_seconds() / 3600.0)
        recency = 0.5 ** (age_h / 96.0)
        category_bonus = min(1.35, 1.0 + 0.08 * max(0, len(categories) - 1))
        primary_bonus = 1.15 if str(row.get("source_type") or "") == "primary" else 1.0
        return reliability * recency * category_bonus * primary_bonus

    def estimate(self, event_type: str, horizon_hours: int, now: datetime) -> ThreatEstimate:
        definition = EVENT_DEFINITIONS[event_type]
        relevant: List[Tuple[Dict[str, Any], Tuple[str, ...], float]] = []
        for row in self.store.evidence():
            try:
                published = parse_time(str(row["published_at"]))
            except Exception:
                continue
            if published > now or published < now - timedelta(days=31):
                continue
            ok, categories = self._event_relevant(event_type, row)
            if not ok:
                continue
            relevant.append((row, categories, self._row_weight(row, now, categories)))

        rows24 = [x for x in relevant if parse_time(str(x[0]["published_at"])) >= now - timedelta(hours=24)]
        rows7 = [x for x in relevant if parse_time(str(x[0]["published_at"])) >= now - timedelta(days=7)]
        rows30 = [x for x in relevant if parse_time(str(x[0]["published_at"])) >= now - timedelta(days=30)]

        weighted24 = sum(x[2] for x in rows24)
        weighted7 = sum(x[2] for x in rows7)
        weighted30 = sum(x[2] for x in rows30)
        source_count = len({str(x[0].get("source") or "") for x in rows7 if x[0].get("source")})
        primary_count = len({str(x[0].get("source") or "") for x in rows30 if str(x[0].get("source_type") or "") == "primary"})
        categories = sorted({c for _, cats, _ in rows30 for c in cats})

        signal = (
            0.42 * math.log1p(weighted24)
            + 0.30 * math.log1p(weighted7)
            + 0.12 * math.log1p(weighted30)
            + 0.07 * min(source_count, 5)
            + 0.10 * min(primary_count, 2)
            + 0.10 * min(len(categories), 6)
        )
        signal = min(3.2, max(0.0, signal))
        horizon_factor = {168: 0.90, 720: 1.00, 2160: 1.10}[horizon_hours]
        prior = self._event_prior(event_type, horizon_hours)
        prior_logit = math.log(prior / (1.0 - prior))
        probability = 1.0 / (1.0 + math.exp(-(prior_logit + signal * horizon_factor)))
        probability = clamp(probability, 0.001, float(definition["cap"]))

        confidence = clamp(
            0.12
            + 0.09 * min(source_count, 5)
            + 0.07 * min(primary_count, 2)
            + 0.06 * min(len(categories), 6)
            + 0.08 * min(1.0, len(rows7) / 5.0),
            0.10,
            0.82,
        )

        strongest = sorted(rows30, key=lambda x: (x[2], str(x[0].get("published_at") or "")), reverse=True)[:12]
        return ThreatEstimate(
            event_type=event_type,
            label=str(definition["label"]),
            target=str(definition["target"]),
            generated_at=iso_z(now),
            horizon_hours=horizon_hours,
            prior_probability=round(prior, 6),
            predicted_probability=round(probability, 6),
            confidence=round(confidence, 6),
            signal_score=round(signal, 6),
            evidence_24h=len(rows24),
            evidence_7d=len(rows7),
            evidence_30d=len(rows30),
            independent_sources_7d=source_count,
            primary_sources_30d=primary_count,
            precursor_categories=tuple(categories),
            supporting_evidence_ids=tuple(str(x[0].get("evidence_id")) for x in strongest if x[0].get("evidence_id")),
        )

    def current_estimates(self, now: datetime) -> List[ThreatEstimate]:
        return [
            self.estimate(event_type, horizon, now)
            for event_type in EVENT_DEFINITIONS
            for horizon in FORECAST_HORIZONS_H
        ]

    @staticmethod
    def _freeze_bucket(now: datetime, force: bool) -> datetime:
        if force:
            return now.replace(minute=0, second=0, microsecond=0)
        return now.replace(hour=DAILY_FREEZE_HOUR_UTC, minute=0, second=0, microsecond=0)

    def freeze(self, estimates: Sequence[ThreatEstimate], now: datetime, *, force: bool = False) -> List[EventForecast]:
        if not force and now.hour != DAILY_FREEZE_HOUR_UTC:
            return []
        bucket = self._freeze_bucket(now, force)
        batch_kind = "manual" if force else "daily"
        batch_id = stable_id("gse-threat-batch", batch_kind, iso_z(bucket))
        if batch_id in {str(row.get("batch_id")) for row in self.store.forecasts()}:
            return []

        forecasts: List[EventForecast] = []
        for estimate in estimates:
            forecasts.append(EventForecast(
                forecast_id=stable_id("gse-threat-forecast", batch_id, estimate.event_type, estimate.horizon_hours),
                batch_id=batch_id,
                event_type=estimate.event_type,
                label=estimate.label,
                target=estimate.target,
                forecast_at=iso_z(now),
                target_at=iso_z(now + timedelta(hours=estimate.horizon_hours)),
                horizon_hours=estimate.horizon_hours,
                prior_probability=estimate.prior_probability,
                predicted_probability=estimate.predicted_probability,
                confidence=estimate.confidence,
                signal_score=estimate.signal_score,
                precursor_categories=estimate.precursor_categories,
                supporting_evidence_ids=estimate.supporting_evidence_ids,
            ))
        _append_unique(self.store.forecasts_path, (row.to_dict() for row in forecasts), "forecast_id")
        return forecasts

    @staticmethod
    def _realization_match(event_type: str, row: Mapping[str, Any]) -> bool:
        text = _normalize(f"{row.get('title','')} {row.get('text','')}")
        if not _contains_any(text, ACTOR_TERMS):
            return False
        if _contains_any(text, HYPOTHETICAL_MARKERS):
            return False
        definition = EVENT_DEFINITIONS[event_type]
        if not _contains_any(text, tuple(definition["target_terms"])):
            return False
        return _contains_any(text, REALIZATION_TERMS)

    def _realization_evidence(self, forecast: Mapping[str, Any]) -> List[Dict[str, Any]]:
        start = parse_time(str(forecast["forecast_at"]))
        target = parse_time(str(forecast["target_at"]))
        event_type = str(forecast["event_type"])
        out: List[Dict[str, Any]] = []
        for row in self.store.evidence():
            try:
                published = parse_time(str(row["published_at"]))
            except Exception:
                continue
            if published <= start or published > target:
                continue
            if self._realization_match(event_type, row):
                out.append(row)
        return out

    def _coverage(self, forecast: Mapping[str, Any]) -> Tuple[int, int]:
        start = parse_time(str(forecast["forecast_at"]))
        target = parse_time(str(forecast["target_at"]))
        rows = [
            row for row in self.store.cycles()
            if row.get("scan_at")
            and start < parse_time(str(row["scan_at"])) <= target
        ]
        days = {parse_time(str(row["scan_at"])).date().isoformat() for row in rows}
        return len(rows), len(days)

    @staticmethod
    def _coverage_eligible(horizon_hours: int, cycles: int, days: int) -> bool:
        horizon_days = max(1, math.ceil(horizon_hours / 24.0))
        min_cycles = max(3, math.ceil(horizon_days * 6.0))  # at least 25% of hourly cadence
        min_days = max(1, math.ceil(horizon_days * 0.60))
        return cycles >= min_cycles and days >= min_days

    def verify_due(self, now: datetime) -> List[EventVerification]:
        existing = {str(row.get("forecast_id")) for row in self.store.verifications()}
        out: List[EventVerification] = []
        for forecast in self.store.forecasts():
            if str(forecast.get("forecast_id")) in existing:
                continue
            try:
                target = parse_time(str(forecast["target_at"]))
            except Exception:
                continue
            if target > now:
                continue

            matches = self._realization_evidence(forecast)
            primary = [row for row in matches if str(row.get("source_type") or "") == "primary"]
            secondary_sources = {
                str(row.get("source") or "")
                for row in matches
                if str(row.get("source_type") or "") != "primary" and float(row.get("reliability") or 0.0) >= 0.60
            }
            confirmed = bool(primary) or len(secondary_sources) >= MIN_SECONDARY_CONFIRMATIONS
            cycles, days = self._coverage(forecast)
            eligible = confirmed or self._coverage_eligible(int(forecast["horizon_hours"]), cycles, days)
            p = float(forecast["predicted_probability"])
            prior = float(forecast["prior_probability"])
            y = 1.0 if confirmed else 0.0
            verification = EventVerification(
                verification_id=stable_id("gse-threat-verification", forecast["forecast_id"], iso_z(now)),
                forecast_id=str(forecast["forecast_id"]),
                event_type=str(forecast["event_type"]),
                horizon_hours=int(forecast["horizon_hours"]),
                predicted_probability=p,
                prior_probability=prior,
                outcome=confirmed,
                verified_at=iso_z(now),
                realization_evidence_ids=tuple(str(row.get("evidence_id")) for row in matches if row.get("evidence_id")),
                confirmation_sources=len({str(row.get("source") or "") for row in matches if row.get("source")}),
                calibration_eligible=eligible,
                coverage_cycles=cycles,
                coverage_days=days,
                brier_score=round((p - y) ** 2, 8),
                prior_brier_score=round((prior - y) ** 2, 8),
                log_loss=round(log_loss(p, confirmed), 8),
            )
            out.append(verification)
        _append_unique(self.store.verifications_path, (row.to_dict() for row in out), "verification_id")
        return out

    @staticmethod
    def _metrics(rows: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
        eligible = [row for row in rows if bool(row.get("calibration_eligible", True))]
        if not eligible:
            return {
                "count": 0,
                "positive_count": 0,
                "status": "awaiting_eligible_outcomes",
                "mean_brier": None,
                "mean_prior_brier": None,
                "delta_brier_vs_prior": None,
                "mean_log_loss": None,
                "mean_predicted": None,
                "positive_rate": None,
                "bias": None,
            }
        probs = [float(row["predicted_probability"]) for row in eligible]
        ys = [1.0 if bool(row["outcome"]) else 0.0 for row in eligible]
        brier = mean(float(row["brier_score"]) for row in eligible)
        prior_brier = mean(float(row["prior_brier_score"]) for row in eligible)
        positives = int(sum(ys))
        status = "measuring"
        if len(eligible) < 30:
            status = "insufficient_sample"
        elif positives < 3:
            status = "rare_event_no_positive_class_yet"
        return {
            "count": len(eligible),
            "positive_count": positives,
            "status": status,
            "mean_brier": round(brier, 8),
            "mean_prior_brier": round(prior_brier, 8),
            "delta_brier_vs_prior": round(brier - prior_brier, 8),
            "mean_log_loss": round(mean(float(row["log_loss"]) for row in eligible), 8),
            "mean_predicted": round(mean(probs), 8),
            "positive_rate": round(mean(ys), 8),
            "bias": round(mean(probs) - mean(ys), 8),
        }

    def calibration(self) -> Dict[str, Any]:
        rows = self.store.verifications()
        by_event = {
            event_type: self._metrics([row for row in rows if str(row.get("event_type")) == event_type])
            for event_type in EVENT_DEFINITIONS
        }
        by_horizon = {
            str(horizon): self._metrics([row for row in rows if int(row.get("horizon_hours") or 0) == horizon])
            for horizon in FORECAST_HORIZONS_H
        }
        report = {
            "schema_version": SCHEMA_VERSION,
            "mode": MODE,
            "overall": self._metrics(rows),
            "by_event_type": by_event,
            "by_horizon_hours": by_horizon,
            "baseline": "stored_engineering_seed_prior",
            "automatic_tuning_enabled": False,
            "automatic_promotion_enabled": False,
        }
        _write_json(self.store.calibration_path, report)
        return report

    def _record_cycle(self, now: datetime) -> int:
        evidence = []
        for row in self.store.evidence():
            try:
                published = parse_time(str(row["published_at"]))
            except Exception:
                continue
            if now - timedelta(days=30) <= published <= now:
                evidence.append(row)
        cycle = {
            "cycle_id": stable_id("gse-threat-cycle", now.strftime("%Y-%m-%dT%H")),
            "scan_at": iso_z(now),
            "evidence_30d": len(evidence),
            "source_count_30d": len({str(row.get("source") or "") for row in evidence if row.get("source")}),
        }
        return _append_unique(self.store.cycles_path, (cycle,), "cycle_id")

    def run(self, now: datetime, *, force_freeze: bool = False) -> Dict[str, Any]:
        now = now.astimezone(UTC)
        cycle_written = self._record_cycle(now)
        estimates = self.current_estimates(now)
        frozen = self.freeze(estimates, now, force=force_freeze)
        verified = self.verify_due(now)
        calibration = self.calibration()
        state = {
            "schema_version": SCHEMA_VERSION,
            "mode": MODE,
            "generated_at": iso_z(now),
            "model_status": "prospective_uncalibrated_seed",
            "probability_semantics": (
                "Research probability that qualifying evidence of the defined armed event "
                "will be observed by the horizon; initial mapping is not an authoritative intelligence estimate."
            ),
            "current_estimates": [row.to_dict() for row in estimates],
            "last_cycle": {
                "coverage_cycle_written": cycle_written,
                "forecasts_frozen": len(frozen),
                "verifications_added": len(verified),
                "eligible_verification_count_total": int(calibration["overall"]["count"]),
                "mean_brier": calibration["overall"]["mean_brier"],
                "delta_brier_vs_prior": calibration["overall"]["delta_brier_vs_prior"],
            },
            "cadence": {
                "estimate_refresh": "hourly_after_gse_evidence_scan",
                "forecast_freeze": "daily_00_UTC",
                "verification": "hourly_when_due",
                "forecast_horizons_hours": list(FORECAST_HORIZONS_H),
            },
            "controls": {
                "research_only": True,
                "trade_execution_enabled": False,
                "policy_output_enabled": False,
                "belief_core_writeback_enabled": False,
                "decision_engine_connected": False,
                "automatic_tuning_enabled": False,
                "automatic_promotion_enabled": False,
                "frozen_forecast_rewrite_enabled": False,
            },
        }
        _write_json(self.store.state_path, state)
        return state


def main() -> int:
    parser = argparse.ArgumentParser(description="Run GSE Event Probability / Threat Engine")
    parser.add_argument("--state-dir", required=True)
    parser.add_argument("--now", help="ISO timestamp override")
    parser.add_argument("--force-freeze", action="store_true")
    args = parser.parse_args()
    now = parse_time(args.now) if args.now else datetime.now(UTC)
    state = EventProbabilityThreatEngine(Path(args.state_dir)).run(now, force_freeze=args.force_freeze)
    print(json.dumps({
        "mode": state["mode"],
        "model_status": state["model_status"],
        "generated_at": state["generated_at"],
        "last_cycle": state["last_cycle"],
    }, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
