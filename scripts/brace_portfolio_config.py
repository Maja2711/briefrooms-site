#!/usr/bin/env python3
"""Configuration and immutable policy for BRACE Portfolio Engine."""
from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Mapping

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG_PATH = ROOT / "data" / "portfolio10k" / "config.json"
DEFAULT_ADAPTIVE_POLICY_PATH = ROOT / "data" / "portfolio10k" / "adaptive_policy.json"

AUTONOMY_MODES = {
    "MONITOR_ONLY",
    "RECOMMEND_ONLY",
    "PAPER_EXECUTION",
    "AUTO_EXECUTION",
}
METHODOLOGY_STATUSES = {
    "ACTIVE_BASELINE",
    "CANDIDATE",
    "TESTING",
    "SHADOW",
    "PROBATIONARY_CONTROL",
    "ACTIVE_PAPER_CONTROL",
    "APPROVED",
    "ACTIVE",
    "DEGRADED",
    "SAFE_MODE",
    "SUSPENDED",
    "FALLBACK_BASELINE",
    "RETIRED",
    "RETIRED_BASELINE",
}

ADAPTIVE_FIELDS = {
    "minimum_confidence": (0.60, 0.75),
    "minimum_score_improvement": (6.5, 10.5),
    "minimum_expected_alpha": (0.0175, 0.0400),
}


def _canonical_sha256(payload: Any) -> str:
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _load_adaptive_overrides(
    adaptive_path: Path,
    base_raw: Mapping[str, Any],
) -> tuple[Dict[str, float], Dict[str, Any]]:
    if not adaptive_path.exists():
        return {}, {"status": "NOT_CONFIGURED", "applied": False}
    adaptive = json.loads(adaptive_path.read_text(encoding="utf-8"))
    if adaptive.get("schema_version") != "brace-adaptive-policy-v1":
        raise ValueError("Unsupported BRACE adaptive policy schema")
    if adaptive.get("never_apply_to_real_broker") is not True:
        raise ValueError("Adaptive policy must explicitly prohibit real-broker use")
    if adaptive.get("apply_to_shadow_decisions") is not True:
        return {}, {
            "status": str(adaptive.get("status") or "INACTIVE"),
            "applied": False,
            "content_sha256": adaptive.get("content_sha256"),
        }
    expected_base_hash = str(adaptive.get("base_config_sha256") or "")
    actual_base_hash = _canonical_sha256(base_raw)
    if expected_base_hash != actual_base_hash:
        raise ValueError("Adaptive policy was trained against a different base configuration")
    overrides = adaptive.get("active_overrides") or {}
    unknown = set(overrides) - set(ADAPTIVE_FIELDS)
    if unknown:
        raise ValueError(f"Adaptive policy contains non-whitelisted fields: {sorted(unknown)}")
    clean: Dict[str, float] = {}
    for name, value in overrides.items():
        number = float(value)
        low, high = ADAPTIVE_FIELDS[name]
        if not low <= number <= high:
            raise ValueError(f"Adaptive value for {name} is outside governed bounds")
        clean[name] = number
    return clean, {
        "status": str(adaptive.get("status") or "ACTIVE_SHADOW_PARAMETERS"),
        "applied": bool(clean),
        "content_sha256": adaptive.get("content_sha256"),
        "generated_at": adaptive.get("generated_at"),
        "scope": "BRACE challenger shadow decisions only",
    }


@dataclass(frozen=True)
class EngineConfig:
    target_annual_return: float
    autonomy_mode: str
    max_single_stock_weight: float
    max_broad_etf_weight: float
    max_sector_weight: float
    max_currency_weight: float
    max_region_weight: float
    minimum_position_weight: float
    max_positions: int
    minimum_holding_period_days: int
    rotation_cooldown_days: int
    minimum_confidence: float
    probationary_minimum_confidence: float
    minimum_score_improvement: float
    minimum_expected_alpha: float
    transaction_cost_buffer: float
    max_expected_drawdown: float
    emergency_drawdown: float
    max_annual_turnover: float
    max_weekly_turnover_probation: float
    maximum_missing_instruments: int
    monitoring_max_price_age_hours: float
    analysis_max_price_age_hours: float
    maximum_single_price_jump: float
    minimum_shadow_calendar_days: int
    minimum_shadow_decisions: int
    minimum_shadow_completed_trades: int
    minimum_probation_calendar_days: int
    max_probation_rotations_per_day: int
    max_probation_position_changes_per_week: int
    max_probation_new_position_weight: float
    target_probability_floor: float
    risk_free_rate: float
    safe_mode_on_stale_data: bool
    paper_execution_enabled_after_promotion: bool
    real_broker_integration_enabled: bool

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "EngineConfig":
        policy = raw.get("policy") if "policy" in raw else raw
        values = {field: policy[field] for field in cls.__dataclass_fields__}
        config = cls(**values)
        config.validate()
        return config

    def validate(self) -> None:
        if self.autonomy_mode not in AUTONOMY_MODES:
            raise ValueError(f"Unsupported autonomy_mode: {self.autonomy_mode}")
        if self.autonomy_mode == "AUTO_EXECUTION":
            raise ValueError("Real-broker AUTO_EXECUTION is prohibited")
        if self.real_broker_integration_enabled:
            raise ValueError("Real broker integration must remain disabled")
        if not 0 < self.target_annual_return < 1:
            raise ValueError("target_annual_return must be between 0 and 1")
        for name in (
            "max_single_stock_weight",
            "max_broad_etf_weight",
            "max_sector_weight",
            "max_currency_weight",
            "max_region_weight",
            "minimum_position_weight",
            "minimum_confidence",
            "probationary_minimum_confidence",
            "max_expected_drawdown",
            "emergency_drawdown",
            "max_weekly_turnover_probation",
            "max_probation_new_position_weight",
        ):
            value = float(getattr(self, name))
            if not 0 < value <= 1:
                raise ValueError(f"{name} must be between 0 and 1")
        if self.max_single_stock_weight > 0.18:
            raise ValueError("Single-stock cap cannot exceed 18%")
        if self.max_broad_etf_weight > 0.30:
            raise ValueError("Broad-ETF cap cannot exceed 30%")
        if self.max_positions < 2:
            raise ValueError("max_positions is too small")


def load_config(
    path: Path = DEFAULT_CONFIG_PATH,
    adaptive_path: Path | None = None,
) -> tuple[EngineConfig, Dict[str, Any]]:
    base_raw = json.loads(path.read_text(encoding="utf-8"))
    merged = deepcopy(base_raw)
    use_adaptive = adaptive_path is not None or path.resolve() == DEFAULT_CONFIG_PATH.resolve()
    metadata: Dict[str, Any] = {"status": "DISABLED_FOR_NONDEFAULT_CONFIG", "applied": False}
    if use_adaptive:
        selected_path = adaptive_path or DEFAULT_ADAPTIVE_POLICY_PATH
        overrides, metadata = _load_adaptive_overrides(selected_path, base_raw)
        merged.setdefault("policy", {}).update(overrides)
    merged["adaptive_policy_runtime"] = metadata
    return EngineConfig.from_mapping(merged), merged


def public_policy(config: EngineConfig) -> Dict[str, Any]:
    return {
        "target_annual_return": config.target_annual_return,
        "autonomy_mode": config.autonomy_mode,
        "max_single_stock_weight": config.max_single_stock_weight,
        "max_broad_etf_weight": config.max_broad_etf_weight,
        "max_sector_weight": config.max_sector_weight,
        "max_currency_weight": config.max_currency_weight,
        "minimum_position_weight": config.minimum_position_weight,
        "max_positions": config.max_positions,
        "minimum_confidence": config.minimum_confidence,
        "minimum_score_improvement": config.minimum_score_improvement,
        "minimum_expected_alpha": config.minimum_expected_alpha,
        "max_expected_drawdown": config.max_expected_drawdown,
        "minimum_shadow_calendar_days": config.minimum_shadow_calendar_days,
        "minimum_shadow_decisions": config.minimum_shadow_decisions,
        "minimum_shadow_completed_trades": config.minimum_shadow_completed_trades,
        "minimum_probation_calendar_days": config.minimum_probation_calendar_days,
        "paper_execution_only": True,
        "real_broker_integration": False,
    }
