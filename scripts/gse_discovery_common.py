from __future__ import annotations

import hashlib
import html
import json
import os
import re
import urllib.parse
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Sequence

UTC = timezone.utc
MODE = "shadow"
PIPELINE_VERSION = "gse-historical-event-discovery-v1"
CATALOG_VERSION = "gse-historical-event-catalog-effective-v1"
STATE_VERSION = "gse-historical-event-discovery-state-v1"
CANDIDATE_VERSION = "gse-historical-event-candidate-v1"
TARGET_VERIFIED_CLUSTERS = 100
MIN_VERIFY_SCORE = 0.78
MIN_CLASSIFICATION_SCORE = 0.58
DEFAULT_START_DATE = "2014-01-01"
RECENT_OVERLAP_DAYS = 45
FULL_REFRESH_DAYS = 7
REQUEST_SLEEP_SECONDS = 0.08
MAX_DETAIL_FETCHES = 900

OFFICIAL_HOST_SUFFIXES = (
    "treasury.gov", "defense.gov", "un.org", "nato.int",
    "consilium.europa.eu", "state.gov", "mnd.gov.tw",
)

SCENARIO_PROFILES: Mapping[str, Mapping[str, Sequence[str]]] = {
    "middle_east_energy_escalation": {
        "actors": ("iran", "iranian", "israel", "israeli", "houthi", "houthis", "yemen", "hezbollah", "hizballah", "hamas", "hormuz", "persian gulf", "gulf"),
        "actions": ("attack", "strike", "missile", "drone", "military", "designat", "sanction", "tanker", "shipping", "oil", "energy", "rocket", "seiz", "escalat"),
    },
    "russia_ukraine_black_sea_escalation": {
        "actors": ("russia", "russian", "ukraine", "ukrainian", "crimea", "black sea", "kremlin"),
        "actions": ("invasion", "attack", "strike", "missile", "military", "designat", "sanction", "blockade", "port", "export", "pipeline", "occupation", "war", "aggression"),
    },
    "red_sea_shipping_disruption": {
        "actors": ("red sea", "houthi", "houthis", "yemen", "bab el-mandeb", "bab al-mandab"),
        "actions": ("ship", "shipping", "vessel", "tanker", "attack", "strike", "missile", "drone", "seiz", "rerout", "maritime", "designat", "sanction"),
    },
    "china_taiwan_trade_escalation": {
        "actors": ("china", "chinese", "prc", "taiwan", "taiwanese", "taiwan strait", "hong kong"),
        "actions": ("military", "exercise", "blockade", "missile", "designat", "sanction", "export control", "tariff", "trade", "semiconductor", "coerc", "intimidat"),
    },
    "sanctions_escalation": {
        "actors": ("russia", "russian", "ukraine", "iran", "iranian", "china", "chinese", "prc", "taiwan", "hong kong", "belarus", "houthi", "houthis", "yemen", "venezuela"),
        "actions": ("designat", "sanction", "block", "asset freeze", "restrict", "export control", "embargo", "blacklist", "target", "secondary sanction"),
    },
    "grain_export_disruption": {
        "actors": ("grain", "wheat", "corn", "food export", "black sea", "ukraine", "russia"),
        "actions": ("export ban", "blockade", "port", "termination", "suspend", "disrupt", "attack", "shipping", "corridor", "grain initiative", "restrict export"),
    },
}

NEGATIVE_ACTION_TERMS = (
    "removal", "removals", "removed", "delist", "de-list", "general license only",
    "settlement agreement", "civil monetary penalty", "penalty on", "frequently asked question only",
)

ACTOR_BUCKETS: Mapping[str, Sequence[str]] = {
    "russia_ukraine": ("russia", "russian", "ukraine", "ukrainian", "crimea", "black sea"),
    "iran": ("iran", "iranian", "hormuz", "persian gulf"),
    "houthi_yemen": ("houthi", "houthis", "yemen", "red sea", "bab el-mandeb", "bab al-mandab"),
    "israel_levant": ("israel", "israeli", "hezbollah", "hizballah", "hamas"),
    "china_taiwan": ("china", "chinese", "prc", "taiwan", "taiwanese", "hong kong", "taiwan strait"),
    "grain_black_sea": ("grain", "wheat", "corn", "food export", "black sea"),
    "belarus": ("belarus", "belarusian"),
    "venezuela": ("venezuela", "venezuelan"),
}

FEATURE_KEYS = (
    "severity", "surprise", "global_scope", "military_relevance", "energy_relevance",
    "shipping_relevance", "sanctions_relevance", "food_relevance", "china_relevance",
)


def clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, float(value)))


def iso_z(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def parse_time(value: Any) -> datetime:
    if isinstance(value, datetime):
        dt = value
    else:
        text = str(value or "").strip()
        if not text:
            raise ValueError("empty timestamp")
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            dt = datetime.fromisoformat(text)
        except ValueError:
            try:
                dt = parsedate_to_datetime(text)
            except Exception:
                dt = None
            if dt is None:
                for fmt in ("%B %d, %Y", "%b %d, %Y", "%m/%d/%Y", "%Y-%m-%d"):
                    try:
                        dt = datetime.strptime(text, fmt)
                        break
                    except ValueError:
                        pass
            if dt is None:
                raise ValueError(f"invalid timestamp: {value}")
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def stable_id(prefix: str, *parts: Any) -> str:
    raw = "|".join(str(part) for part in parts)
    return f"{prefix}-{hashlib.sha256(raw.encode('utf-8')).hexdigest()[:20]}"


def canonical_sha256(payload: Any) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def read_jsonl(path: Path) -> List[Dict[str, Any]]:
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


def append_unique(path: Path, rows: Iterable[Mapping[str, Any]], id_key: str) -> int:
    existing = {str(row.get(id_key)) for row in read_jsonl(path)}
    pending = [dict(row) for row in rows if str(row.get(id_key)) not in existing]
    if not pending:
        return 0
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        for row in pending:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    return len(pending)


def normalize_text(value: Any) -> str:
    text = html.unescape(re.sub(r"<[^>]+>", " ", str(value or "")))
    return re.sub(r"\s+", " ", text).strip().lower()


def strip_tags(value: Any) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", str(value or "")))).strip()


def host_is_official(url: str) -> bool:
    try:
        host = (urllib.parse.urlparse(url).hostname or "").lower().strip(".")
    except Exception:
        return False
    return any(host == suffix or host.endswith("." + suffix) for suffix in OFFICIAL_HOST_SUFFIXES)


def tokenize(text: str) -> set[str]:
    excluded = {"the", "and", "for", "with", "from", "that", "this", "related", "issuance", "update", "updates"}
    return {token for token in re.findall(r"[a-z0-9][a-z0-9-]{2,}", normalize_text(text)) if token not in excluded}


def title_overlap(left: str, right: str) -> float:
    a, b = tokenize(left), tokenize(right)
    return 0.0 if not a or not b else len(a & b) / max(1, min(len(a), len(b)))


def _contains_any(text: str, terms: Sequence[str]) -> bool:
    return any(term in text for term in terms)


def classify_scenarios(title: str, body: str = "") -> List[Dict[str, Any]]:
    raw_title = normalize_text(title)
    segments = [x.strip() for x in re.split(r"[;|]", raw_title) if x.strip()]
    positive = [x for x in segments if not any(term in x for term in NEGATIVE_ACTION_TERMS)]
    if segments and not positive and any(term in raw_title for term in NEGATIVE_ACTION_TERMS):
        return []
    title_n = " ; ".join(positive) if positive else raw_title
    body_n = normalize_text(body)[:12000]
    out: List[Dict[str, Any]] = []
    for scenario, profile in SCENARIO_PROFILES.items():
        actors, actions = tuple(profile["actors"]), tuple(profile["actions"])
        ta, tx = _contains_any(title_n, actors), _contains_any(title_n, actions)
        ba, bx = _contains_any(body_n, actors), _contains_any(body_n, actions)
        if not ((ta and tx) or (ta and bx) or (ba and tx)):
            continue
        actor_score = 1.0 if ta else (0.55 if ba else 0.0)
        action_score = 1.0 if tx else (0.55 if bx else 0.0)
        score = clamp(0.43 * actor_score + 0.42 * action_score + (0.15 if ta and tx else 0.08))
        if score >= MIN_CLASSIFICATION_SCORE:
            out.append({"scenario_type": scenario, "score": round(score, 6)})
    return sorted(out, key=lambda row: (-float(row["score"]), str(row["scenario_type"])))


def detect_actor_buckets(text: str) -> List[str]:
    normalized = normalize_text(text)
    rows = [bucket for bucket, terms in ACTOR_BUCKETS.items() if _contains_any(normalized, terms)]
    return rows or ["other"]


def infer_features(title: str, body: str, scenarios: Sequence[Mapping[str, Any]]) -> Dict[str, float]:
    text = normalize_text(f"{title} {body}")
    severity = 0.96 if _contains_any(text, ("invasion", "direct attack", "ballistic missile", "large-scale", "largest ever", "blockade")) else 0.86 if _contains_any(text, ("strike", "missile", "drone attack", "seiz", "military exercise", "oil facility")) else 0.68 if _contains_any(text, ("sanction", "designat", "export control", "embargo", "asset freeze")) else 0.48
    surprise = 0.84 if _contains_any(text, ("first time", "unprecedented", "surprise", "emergency", "direct attack", "largest ever")) else 0.52
    names = {str(row.get("scenario_type")) for row in scenarios}
    f: Dict[str, float] = {"severity": severity, "surprise": surprise, "global_scope": .72, "military_relevance": .15, "energy_relevance": .10, "shipping_relevance": .10, "sanctions_relevance": .10, "food_relevance": .05, "china_relevance": .05}
    if "middle_east_energy_escalation" in names: f.update(military_relevance=max(f["military_relevance"],.80), energy_relevance=.95, shipping_relevance=max(f["shipping_relevance"],.45), global_scope=.86)
    if "russia_ukraine_black_sea_escalation" in names: f.update(military_relevance=max(f["military_relevance"],.88), energy_relevance=max(f["energy_relevance"],.55), shipping_relevance=max(f["shipping_relevance"],.45), food_relevance=max(f["food_relevance"],.45), global_scope=.88)
    if "red_sea_shipping_disruption" in names: f.update(military_relevance=max(f["military_relevance"],.60), energy_relevance=max(f["energy_relevance"],.55), shipping_relevance=1.0, global_scope=.82)
    if "china_taiwan_trade_escalation" in names: f.update(military_relevance=max(f["military_relevance"],.70), china_relevance=1.0, shipping_relevance=max(f["shipping_relevance"],.50), global_scope=.90)
    if "sanctions_escalation" in names: f.update(sanctions_relevance=1.0, global_scope=max(f["global_scope"],.72))
    if "grain_export_disruption" in names: f.update(food_relevance=1.0, shipping_relevance=max(f["shipping_relevance"],.55), global_scope=max(f["global_scope"],.76))
    return {key: round(clamp(float(f[key])), 6) for key in FEATURE_KEYS}
