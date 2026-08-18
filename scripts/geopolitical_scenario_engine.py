#!/usr/bin/env python3
"""Geopolitical Scenario Engine (GSE) v1.

Architecture:
Geopolitical Evidence -> Scenario Engine -> Transmission Graph -> Multi-Asset Forecast
-> Frozen Forecast -> Verification -> Calibration.

Shadow/research only. This module cannot place orders, size positions, emit policy
output, or auto-tune model weights.
"""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import math
import os
import re
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from statistics import mean
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

UTC = timezone.utc
MODE = "shadow"
SCHEMA_VERSION = 1
FORECAST_HORIZONS_H = (24, 168, 720)
FREEZE_HOURS_UTC = (0, 6, 12, 18)
GDELT_DOC_API = "https://api.gdeltproject.org/api/v2/doc/doc"
OFAC_RECENT_ACTIONS = "https://ofac.treasury.gov/recent-actions"
UN_NEWS_RSS = "https://news.un.org/feed/subscribe/en/news/all/rss.xml"
YAHOO_CHART = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"

# The engine deliberately starts with liquid, globally-relevant proxies/data series.
ASSETS: Mapping[str, Mapping[str, str]] = {
    "BRENT": {"symbol": "BZ=F", "label": "Brent crude oil"},
    "WTI": {"symbol": "CL=F", "label": "WTI crude oil"},
    "GOLD": {"symbol": "GC=F", "label": "Gold"},
    "COPPER": {"symbol": "HG=F", "label": "Copper"},
    "WHEAT": {"symbol": "ZW=F", "label": "Wheat"},
    "NATGAS": {"symbol": "NG=F", "label": "Natural gas"},
    "SPX": {"symbol": "SPY", "label": "S&P 500 proxy"},
    "US10Y": {"symbol": "^TNX", "label": "US 10Y Treasury yield"},
    "USD": {"symbol": "DX-Y.NYB", "label": "US Dollar Index"},
}

# Signed impact: +1 means the scenario is expected to push the named asset/data series up,
# -1 down. Magnitudes are intentionally conservative in v1.
TRANSMISSION_GRAPH: Mapping[str, Mapping[str, float]] = {
    "middle_east_energy_escalation": {
        "BRENT": .95, "WTI": .85, "GOLD": .55, "SPX": -.45, "US10Y": .20, "USD": .20,
    },
    "russia_ukraine_black_sea_escalation": {
        "BRENT": .45, "NATGAS": .65, "WHEAT": .80, "GOLD": .35, "SPX": -.30, "USD": .15,
    },
    "red_sea_shipping_disruption": {
        "BRENT": .35, "WTI": .25, "GOLD": .20, "SPX": -.18, "US10Y": .10,
    },
    "china_taiwan_trade_escalation": {
        "COPPER": -.55, "GOLD": .45, "SPX": -.60, "US10Y": -.20, "USD": .35,
    },
    "sanctions_escalation": {
        "BRENT": .25, "GOLD": .30, "SPX": -.18, "USD": .20,
    },
    "grain_export_disruption": {
        "WHEAT": .90, "GOLD": .10, "SPX": -.10, "US10Y": .10,
    },
}

SCENARIO_RULES: Mapping[str, Mapping[str, Sequence[str]]] = {
    "middle_east_energy_escalation": {
        "strong": ("iran", "israel", "strait of hormuz", "hormuz", "gulf", "oil facility", "missile"),
        "context": ("attack", "strike", "escalat", "sanction", "tanker", "shipping", "military"),
    },
    "russia_ukraine_black_sea_escalation": {
        "strong": ("russia", "ukraine", "black sea", "crimea"),
        "context": ("attack", "strike", "missile", "port", "export", "sanction", "pipeline", "grain"),
    },
    "red_sea_shipping_disruption": {
        "strong": ("red sea", "houthi", "bab el-mandeb", "yemen"),
        "context": ("ship", "tanker", "attack", "missile", "drone", "rerout", "shipping"),
    },
    "china_taiwan_trade_escalation": {
        "strong": ("taiwan", "taiwan strait", "china"),
        "context": ("military", "blockade", "exercise", "tariff", "export control", "sanction", "semiconductor"),
    },
    "sanctions_escalation": {
        "strong": ("sanction", "designation", "export control", "embargo"),
        "context": ("iran", "russia", "china", "venezuela", "belarus", "energy", "shipping", "technology"),
    },
    "grain_export_disruption": {
        "strong": ("grain", "wheat", "corn", "food export"),
        "context": ("black sea", "ukraine", "russia", "port", "export ban", "blockade", "drought"),
    },
}

GDELT_QUERY = (
    '(Iran OR Israel OR Hormuz OR Ukraine OR Russia OR "Black Sea" OR "Red Sea" OR Houthi '
    'OR Taiwan OR sanctions OR blockade OR "export controls" OR grain OR wheat) '
    '(conflict OR attack OR strike OR military OR sanctions OR shipping OR export OR blockade)'
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


def _log_loss(p: float, outcome: bool) -> float:
    p = clamp(p, 1e-9, 1.0 - 1e-9)
    y = 1.0 if outcome else 0.0
    return -(y * math.log(p) + (1.0 - y) * math.log(1.0 - p))


def _read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(path)


def _read_jsonl(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    rows: List[Dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            rows.append(json.loads(line))
        except Exception:
            continue
    return rows


def _append_jsonl_unique(path: Path, rows: Iterable[Mapping[str, Any]], id_key: str) -> int:
    existing = {str(x.get(id_key)) for x in _read_jsonl(path)}
    pending = [dict(x) for x in rows if str(x.get(id_key)) not in existing]
    if not pending:
        return 0
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        for row in pending:
            fh.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    return len(pending)


@dataclass(frozen=True)
class GeoEvidence:
    evidence_id: str
    title: str
    published_at: str
    source: str
    source_type: str
    source_ref: str
    reliability: float
    text: str
    tags: Tuple[str, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        p = asdict(self); p["tags"] = list(self.tags); p["metadata"] = dict(self.metadata); return p


@dataclass(frozen=True)
class Scenario:
    scenario_id: str
    scenario_type: str
    generated_at: str
    probability: float
    confidence: float
    evidence_ids: Tuple[str, ...]
    evidence_7d: int
    evidence_30d: int
    acceleration: float
    rationale: str

    def to_dict(self) -> Dict[str, Any]:
        p = asdict(self); p["evidence_ids"] = list(self.evidence_ids); return p


@dataclass(frozen=True)
class FrozenAssetForecast:
    forecast_id: str
    batch_id: str
    asset: str
    symbol: str
    forecast_at: str
    target_at: str
    horizon_hours: int
    direction: int
    predicted_probability: float
    confidence: float
    impact_magnitude: str
    baseline_value: float
    scenario_snapshot: Tuple[Mapping[str, Any], ...]
    evidence_snapshot: Tuple[Mapping[str, Any], ...]
    mode: str = MODE

    def to_dict(self) -> Dict[str, Any]:
        p = asdict(self)
        p["scenario_snapshot"] = [dict(x) for x in self.scenario_snapshot]
        p["evidence_snapshot"] = [dict(x) for x in self.evidence_snapshot]
        return p


@dataclass(frozen=True)
class ForecastVerification:
    verification_id: str
    forecast_id: str
    asset: str
    horizon_hours: int
    predicted_probability: float
    direction: int
    baseline_value: float
    realized_value: float
    realized_return: float
    outcome: bool
    verified_at: str
    brier_score: float
    log_loss: float
    calibration_eligible: bool = True

    def to_dict(self) -> Dict[str, Any]: return asdict(self)


class HttpClient:
    def __init__(self, timeout: int = 20) -> None:
        self.timeout = timeout
        self.user_agent = os.getenv("GSE_HTTP_USER_AGENT", "BriefRooms-GSE/1.0 research https://briefrooms.com")

    def get_text(self, url: str, accept: str = "text/html,application/xml,text/plain;q=0.9") -> str:
        req = urllib.request.Request(url, headers={"User-Agent": self.user_agent, "Accept": accept})
        with urllib.request.urlopen(req, timeout=self.timeout) as response:
            return response.read().decode("utf-8", "replace")

    def get_json(self, url: str) -> Mapping[str, Any]:
        return json.loads(self.get_text(url, "application/json"))


class GeopoliticalEvidenceAdapter:
    """Collect broad secondary coverage plus primary institutional sanctions actions."""

    name = "geopolitical_evidence"
    version = "1.0.0"

    def __init__(self, client: Optional[HttpClient] = None) -> None:
        self.client = client or HttpClient()

    def _gdelt(self, now: datetime) -> List[GeoEvidence]:
        params = {
            "query": GDELT_QUERY,
            "mode": "ArtList",
            "format": "json",
            "maxrecords": "250",
            "timespan": "30d",
            "sort": "HybridRel",
        }
        url = GDELT_DOC_API + "?" + urllib.parse.urlencode(params)
        payload = self.client.get_json(url)
        out: List[GeoEvidence] = []
        for row in payload.get("articles") or []:
            title = html.unescape(str(row.get("title") or "")).strip()
            source_ref = str(row.get("url") or "").strip()
            if not title or not source_ref:
                continue
            seen = str(row.get("seendate") or "")
            published = now
            for fmt in ("%Y%m%dT%H%M%SZ", "%Y%m%d%H%M%S"):
                try:
                    published = datetime.strptime(seen, fmt).replace(tzinfo=UTC)
                    break
                except ValueError:
                    pass
            if published < now - timedelta(days=31):
                continue
            domain = str(row.get("domain") or "unknown")
            eid = stable_id("geo", "gdelt", source_ref, iso_z(published))
            out.append(GeoEvidence(
                evidence_id=eid,
                title=title,
                published_at=iso_z(published),
                source=f"GDELT:{domain}",
                source_type="secondary",
                source_ref=source_ref,
                reliability=.64,
                text=title,
                tags=("geopolitics", "gdelt", "30d"),
                metadata={"domain": domain, "language": row.get("language"), "sourcecountry": row.get("sourcecountry")},
            ))
        return out

    def _un_rss(self, now: datetime) -> List[GeoEvidence]:
        xml_text = self.client.get_text(UN_NEWS_RSS, "application/rss+xml,application/xml,text/xml")
        root = ET.fromstring(xml_text)
        out: List[GeoEvidence] = []
        for item in root.findall(".//item"):
            title = html.unescape((item.findtext("title") or "").strip())
            link = (item.findtext("link") or "").strip()
            desc = re.sub(r"<[^>]+>", " ", item.findtext("description") or "")
            published_raw = item.findtext("pubDate") or ""
            try:
                published = parsedate_to_datetime(published_raw).astimezone(UTC)
            except Exception:
                published = now
            if not title or not link or published < now - timedelta(days=31):
                continue
            text = re.sub(r"\s+", " ", f"{title} {html.unescape(desc)}").strip()
            out.append(GeoEvidence(
                evidence_id=stable_id("geo", "un", link, iso_z(published)),
                title=title,
                published_at=iso_z(published),
                source="United Nations News",
                source_type="primary",
                source_ref=link,
                reliability=.86,
                text=text[:4000],
                tags=("geopolitics", "un", "institutional"),
            ))
        return out

    def _ofac(self, now: datetime) -> List[GeoEvidence]:
        page = self.client.get_text(OFAC_RECENT_ACTIONS)
        clean = html.unescape(re.sub(r"<[^>]+>", " ", page))
        clean = re.sub(r"\s+", " ", clean)
        pattern = re.compile(
            r"(?P<title>(?:Iran|Russia|Ukraine|Venezuela|Belarus|China|Hong Kong|Cuba|Sudan|Syria|Counter Terrorism|Non-Proliferation)[^\.]{3,220}?)\s+"
            r"(?P<date>January|February|March|April|May|June|July|August|September|October|November|December)\s+(?P<day>\d{1,2}),\s+(?P<year>\d{4})",
            re.I,
        )
        months = {m: i for i, m in enumerate(("January","February","March","April","May","June","July","August","September","October","November","December"), 1)}
        out: List[GeoEvidence] = []
        for match in pattern.finditer(clean):
            try:
                published = datetime(int(match.group("year")), months[match.group("date").capitalize()], int(match.group("day")), tzinfo=UTC)
            except Exception:
                continue
            if published < now - timedelta(days=31):
                continue
            title = re.sub(r"\s+", " ", match.group("title")).strip(" -;")
            out.append(GeoEvidence(
                evidence_id=stable_id("geo", "ofac", title, published.date().isoformat()),
                title=title,
                published_at=iso_z(published),
                source="U.S. Treasury OFAC",
                source_type="primary",
                source_ref=OFAC_RECENT_ACTIONS,
                reliability=.98,
                text=title,
                tags=("geopolitics", "sanctions", "ofac", "primary_source"),
            ))
        return out

    def run(self, now: datetime) -> Tuple[GeoEvidence, ...]:
        rows: List[GeoEvidence] = []
        for collector in (self._gdelt, self._un_rss, self._ofac):
            try:
                rows.extend(collector(now))
            except Exception:
                continue
        dedup: Dict[str, GeoEvidence] = {}
        for row in rows:
            dedup[row.evidence_id] = row
        return tuple(sorted(dedup.values(), key=lambda x: x.published_at))


class ScenarioEngine:
    name = "geopolitical_scenario_engine"
    version = "1.0.0"

    @staticmethod
    def _matches(text: str, rule: Mapping[str, Sequence[str]]) -> bool:
        value = text.lower()
        strong = sum(1 for term in rule["strong"] if term in value)
        context = sum(1 for term in rule["context"] if term in value)
        return strong >= 2 or (strong >= 1 and context >= 1)

    def build(self, evidence: Sequence[GeoEvidence], now: datetime) -> Tuple[Scenario, ...]:
        scenarios: List[Scenario] = []
        for scenario_type, rule in SCENARIO_RULES.items():
            matched = [e for e in evidence if self._matches(f"{e.title} {e.text}", rule)]
            if not matched:
                continue
            rows30 = [e for e in matched if parse_time(e.published_at) >= now - timedelta(days=30)]
            rows7 = [e for e in matched if parse_time(e.published_at) >= now - timedelta(days=7)]
            if not rows30:
                continue
            independent_sources = len({e.source for e in rows30})
            primary_count = sum(1 for e in rows30 if e.source_type == "primary")
            weighted7 = sum(e.reliability * (0.5 ** (max(0.0, (now-parse_time(e.published_at)).total_seconds()/3600.0) / 96.0)) for e in rows7)
            baseline_week = max(.75, len(rows30) / 4.2857)
            acceleration = len(rows7) / baseline_week
            intensity = min(1.0, weighted7 / 5.0)
            probability = clamp(.10 + .28 * intensity + .12 * clamp(acceleration / 2.0) + .04 * min(independent_sources, 4), .10, .72)
            if primary_count:
                probability = clamp(probability + min(.06, .02 * primary_count), .10, .76)
            confidence = clamp(.22 + .10 * min(independent_sources, 4) + .08 * min(primary_count, 2) + .18 * min(1.0, len(rows7) / 5.0), .20, .78)
            representative = sorted(rows30, key=lambda e: (e.source_type == "primary", e.reliability, e.published_at), reverse=True)[:12]
            sid = stable_id("gse-scenario", scenario_type, now.strftime("%Y-%m-%dT%H"))
            scenarios.append(Scenario(
                scenario_id=sid,
                scenario_type=scenario_type,
                generated_at=iso_z(now),
                probability=round(probability, 6),
                confidence=round(confidence, 6),
                evidence_ids=tuple(e.evidence_id for e in representative),
                evidence_7d=len(rows7),
                evidence_30d=len(rows30),
                acceleration=round(acceleration, 6),
                rationale=(
                    f"{len(rows7)} matching events in 7d versus {len(rows30)} in 30d; "
                    f"{independent_sources} source(s), {primary_count} primary-source item(s)."
                ),
            ))
        return tuple(sorted(scenarios, key=lambda x: x.probability * x.confidence, reverse=True))


class MarketVerifier:
    def __init__(self, client: Optional[HttpClient] = None) -> None:
        self.client = client or HttpClient()

    def latest_value(self, symbol: str) -> Optional[float]:
        url = YAHOO_CHART.format(symbol=urllib.parse.quote(symbol, safe="")) + "?range=5d&interval=1h&includePrePost=true"
        try:
            payload = self.client.get_json(url)
            result = ((payload.get("chart") or {}).get("result") or [None])[0]
            closes = (((result or {}).get("indicators") or {}).get("quote") or [{}])[0].get("close") or []
            values = [float(v) for v in closes if v is not None and math.isfinite(float(v))]
            return values[-1] if values else None
        except Exception:
            return None


class TransmissionGraph:
    def asset_score(self, asset: str, scenarios: Sequence[Scenario]) -> Tuple[float, List[Scenario]]:
        contributors: List[Scenario] = []
        score = 0.0
        for scenario in scenarios:
            impact = float(TRANSMISSION_GRAPH.get(scenario.scenario_type, {}).get(asset, 0.0))
            if not impact:
                continue
            score += impact * scenario.probability * scenario.confidence
            contributors.append(scenario)
        return max(-1.0, min(1.0, score)), contributors


class GSEStore:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self.state_path = root / "gse_state.json"
        self.evidence_path = root / "gse_evidence.jsonl"
        self.forecasts_path = root / "gse_forecasts.jsonl"
        self.verifications_path = root / "gse_verifications.jsonl"
        self.calibration_path = root / "gse_calibration.json"

    def state(self) -> Dict[str, Any]:
        return _read_json(self.state_path, {"schema_version": SCHEMA_VERSION, "mode": MODE})

    def evidence(self) -> List[Dict[str, Any]]: return _read_jsonl(self.evidence_path)
    def forecasts(self) -> List[Dict[str, Any]]: return _read_jsonl(self.forecasts_path)
    def verifications(self) -> List[Dict[str, Any]]: return _read_jsonl(self.verifications_path)

    def save_state(self, payload: Mapping[str, Any]) -> None: _write_json(self.state_path, dict(payload))


class GeopoliticalScenarioEngine:
    def __init__(self, root: Path, *, evidence_adapter: Optional[GeopoliticalEvidenceAdapter] = None,
                 market: Optional[MarketVerifier] = None) -> None:
        self.store = GSEStore(root)
        self.evidence_adapter = evidence_adapter or GeopoliticalEvidenceAdapter()
        self.scenario_engine = ScenarioEngine()
        self.graph = TransmissionGraph()
        self.market = market or MarketVerifier()

    @staticmethod
    def _freeze_bucket(now: datetime) -> datetime:
        hour = max(h for h in FREEZE_HOURS_UTC if h <= now.hour) if any(h <= now.hour for h in FREEZE_HOURS_UTC) else 0
        return now.replace(hour=hour, minute=0, second=0, microsecond=0)

    def collect(self, now: datetime) -> Tuple[Tuple[GeoEvidence, ...], Tuple[Scenario, ...], int]:
        evidence = self.evidence_adapter.run(now)
        written = _append_jsonl_unique(self.store.evidence_path, (e.to_dict() for e in evidence), "evidence_id")
        all_rows = [GeoEvidence(
            evidence_id=str(e["evidence_id"]), title=str(e["title"]), published_at=str(e["published_at"]),
            source=str(e["source"]), source_type=str(e["source_type"]), source_ref=str(e["source_ref"]),
            reliability=float(e["reliability"]), text=str(e.get("text") or e.get("title") or ""),
            tags=tuple(e.get("tags") or ()), metadata=dict(e.get("metadata") or {}),
        ) for e in self.store.evidence() if parse_time(e["published_at"]) >= now - timedelta(days=31)]
        scenarios = self.scenario_engine.build(all_rows, now)
        return evidence, scenarios, written

    def freeze(self, scenarios: Sequence[Scenario], now: datetime, *, force: bool = False) -> List[FrozenAssetForecast]:
        bucket = self._freeze_bucket(now)
        if not force and now.hour not in FREEZE_HOURS_UTC:
            return []
        batch_id = stable_id("gse-batch", iso_z(bucket))
        existing_batches = {str(x.get("batch_id")) for x in self.store.forecasts()}
        if batch_id in existing_batches and not force:
            return []

        evidence_by_id = {e["evidence_id"]: e for e in self.store.evidence()}
        forecasts: List[FrozenAssetForecast] = []
        for asset, meta in ASSETS.items():
            score, contributors = self.graph.asset_score(asset, scenarios)
            if not contributors or abs(score) < .025:
                continue
            baseline = self.market.latest_value(meta["symbol"])
            if baseline is None:
                continue
            direction = 1 if score > 0 else -1
            strength = abs(score)
            probability = clamp(.50 + .34 * math.tanh(2.0 * strength), .51, .82)
            confidence = clamp(mean(s.confidence for s in contributors) * min(1.0, .55 + .15 * len(contributors)), .20, .80)
            magnitude = "large" if strength >= .45 else "medium" if strength >= .20 else "small"
            scenario_snapshot = tuple(s.to_dict() for s in contributors)
            evidence_ids: List[str] = []
            for s in contributors:
                evidence_ids.extend(s.evidence_ids)
            evidence_snapshot = tuple(dict(evidence_by_id[eid]) for eid in dict.fromkeys(evidence_ids) if eid in evidence_by_id)
            for horizon in FORECAST_HORIZONS_H:
                target = now + timedelta(hours=horizon)
                forecasts.append(FrozenAssetForecast(
                    forecast_id=stable_id("gse-forecast", batch_id, asset, horizon, direction),
                    batch_id=batch_id,
                    asset=asset,
                    symbol=meta["symbol"],
                    forecast_at=iso_z(now),
                    target_at=iso_z(target),
                    horizon_hours=horizon,
                    direction=direction,
                    predicted_probability=round(probability, 6),
                    confidence=round(confidence, 6),
                    impact_magnitude=magnitude,
                    baseline_value=round(float(baseline), 8),
                    scenario_snapshot=scenario_snapshot,
                    evidence_snapshot=evidence_snapshot,
                ))
        _append_jsonl_unique(self.store.forecasts_path, (f.to_dict() for f in forecasts), "forecast_id")
        return forecasts

    def verify_due(self, now: datetime) -> List[ForecastVerification]:
        existing = {str(v.get("forecast_id")) for v in self.store.verifications()}
        out: List[ForecastVerification] = []
        value_cache: Dict[str, Optional[float]] = {}
        for f in self.store.forecasts():
            if str(f.get("forecast_id")) in existing or parse_time(f["target_at"]) > now:
                continue
            symbol = str(f["symbol"])
            if symbol not in value_cache:
                value_cache[symbol] = self.market.latest_value(symbol)
            realized = value_cache[symbol]
            if realized is None:
                continue
            baseline = float(f["baseline_value"])
            if abs(baseline) < 1e-12:
                continue
            ret = (float(realized) / baseline) - 1.0
            direction = int(f["direction"])
            # Tiny moves are treated as no confirmation rather than directional success.
            threshold = .001 if str(f["asset"]) not in {"US10Y"} else .0005
            outcome = (ret >= threshold) if direction > 0 else (ret <= -threshold)
            p = float(f["predicted_probability"])
            v = ForecastVerification(
                verification_id=stable_id("gse-verification", f["forecast_id"], iso_z(now)),
                forecast_id=str(f["forecast_id"]),
                asset=str(f["asset"]),
                horizon_hours=int(f["horizon_hours"]),
                predicted_probability=p,
                direction=direction,
                baseline_value=baseline,
                realized_value=round(float(realized), 8),
                realized_return=round(ret, 8),
                outcome=bool(outcome),
                verified_at=iso_z(now),
                brier_score=round((p - (1.0 if outcome else 0.0)) ** 2, 8),
                log_loss=round(_log_loss(p, bool(outcome)), 8),
            )
            out.append(v)
        _append_jsonl_unique(self.store.verifications_path, (v.to_dict() for v in out), "verification_id")
        return out

    def calibration(self) -> Dict[str, Any]:
        rows = [r for r in self.store.verifications() if bool(r.get("calibration_eligible", True))]
        def metrics(group: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
            if not group:
                return {"count": 0, "status": "awaiting_outcomes", "mean_brier": None, "mean_log_loss": None, "accuracy": None, "bias": None}
            probs = [float(x["predicted_probability"]) for x in group]
            ys = [1.0 if bool(x["outcome"]) else 0.0 for x in group]
            n = len(group)
            status = "insufficient_sample" if n < 30 else "measuring"
            return {
                "count": n,
                "status": status,
                "mean_brier": round(mean(float(x["brier_score"]) for x in group), 6),
                "mean_log_loss": round(mean(float(x["log_loss"]) for x in group), 6),
                "accuracy": round(mean(ys), 6),
                "mean_predicted": round(mean(probs), 6),
                "bias": round(mean(probs) - mean(ys), 6),
            }
        by_asset: Dict[str, Any] = {}
        by_horizon: Dict[str, Any] = {}
        for asset in sorted({str(r["asset"]) for r in rows}):
            by_asset[asset] = metrics([r for r in rows if str(r["asset"]) == asset])
        for horizon in sorted({int(r["horizon_hours"]) for r in rows}):
            by_horizon[str(horizon)] = metrics([r for r in rows if int(r["horizon_hours"]) == horizon])
        report = {
            "schema_version": SCHEMA_VERSION,
            "mode": MODE,
            "overall": metrics(rows),
            "by_asset": by_asset,
            "by_horizon_hours": by_horizon,
            "automatic_tuning_enabled": False,
        }
        _write_json(self.store.calibration_path, report)
        return report

    def run(self, now: datetime, *, force_freeze: bool = False) -> Dict[str, Any]:
        new_evidence, scenarios, evidence_written = self.collect(now)
        forecasts = self.freeze(scenarios, now, force=force_freeze)
        verifications = self.verify_due(now)
        calibration = self.calibration()
        state = self.store.state()
        state.update({
            "schema_version": SCHEMA_VERSION,
            "mode": MODE,
            "last_run_at": iso_z(now),
            "last_status": {
                "evidence_collected": len(new_evidence),
                "evidence_written": evidence_written,
                "active_scenarios": len(scenarios),
                "forecasts_frozen": len(forecasts),
                "forecasts_verified": len(verifications),
                "verification_count_total": calibration["overall"]["count"],
            },
            "active_scenarios": [s.to_dict() for s in scenarios[:12]],
            "controls": {
                "trade_execution_enabled": False,
                "policy_output_enabled": False,
                "automatic_tuning_enabled": False,
                "decision_engine_connected": False,
            },
            "cadence": {
                "evidence_scan": "hourly_24x7",
                "scenario_and_forecast_freeze": "00,06,12,18_UTC",
                "verification": "hourly_when_due",
                "forecast_horizons_hours": list(FORECAST_HORIZONS_H),
            },
        })
        self.store.save_state(state)
        return state["last_status"]


def main() -> int:
    parser = argparse.ArgumentParser(description="Run Geopolitical Scenario Engine in shadow mode")
    parser.add_argument("--state-dir", default=os.environ.get("GSE_STATE_DIR", ".belief_runtime/gse"))
    parser.add_argument("--now", help="ISO timestamp override")
    parser.add_argument("--force-freeze", action="store_true")
    args = parser.parse_args()
    now = parse_time(args.now) if args.now else datetime.now(UTC)
    engine = GeopoliticalScenarioEngine(Path(args.state_dir))
    status = engine.run(now, force_freeze=args.force_freeze)
    print(json.dumps(status, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
