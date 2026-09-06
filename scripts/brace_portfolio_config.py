#!/usr/bin/env python3
"""Configuration and immutable policy for BRACE Portfolio Engine."""
from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from dataclasses import MISSING, dataclass
from pathlib import Path
from typing import Any, Dict, Mapping

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG_PATH = ROOT / "data" / "portfolio10k" / "config.json"
DEFAULT_ADAPTIVE_POLICY_PATH = ROOT / "data" / "portfolio10k" / "adaptive_policy.json"
DEFAULT_PORTFOLIO_EVOLUTION_POLICY_PATH = (
    ROOT / "data" / "portfolio10k" / "portfolio_evolution_policy.json"
)

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

# Existing gate learner. Kept separate from portfolio construction evolution so the
# two autonomous loops cannot overwrite each other's policy state.
ADAPTIVE_FIELDS = {
    "minimum_confidence": (0.60, 0.75),
    "minimum_score_improvement": (6.5, 10.5),
    "minimum_expected_alpha": (0.0175, 0.0400),
}

# Mutable portfolio-construction zone. These values may be changed by the
# autonomous portfolio evolution loop, but only inside the governed bounds.
PORTFOLIO_EVOLUTION_FIELDS = {
    "portfolio_risk_aversion": (0.20, 0.90),
    "portfolio_drawdown_penalty": (0.20, 1.25),
    "portfolio_turnover_penalty": (0.10, 0.60),
    "portfolio_diversification_penalty": (0.00, 0.40),
    "portfolio_cash_floor": (0.00, 0.50),
    "portfolio_rebalance_threshold": (0.005, 0.05),
    "portfolio_max_active_positions": (2.0, 12.0),
}

# Safety Kernel: autonomous learners are never allowed to write these fields.
IMMUTABLE_SAFETY_FIELDS = frozenset(
    {
        "autonomy_mode",
        "max_single_stock_weight",
        "max_broad_etf_weight",
        "max_sector_weight",
        "max_currency_weight",
        "max_region_weight",
        "minimum_position_weight",
        "max_positions",
        "max_expected_drawdown",
        "emergency_drawdown",
        "max_annual_turnover",
        "max_weekly_turnover_probation",
        "maximum_missing_instruments",
        "monitoring_max_price_age_hours",
        "analysis_max_price_age_hours",
        "maximum_single_price_jump",
        "max_probation_rotations_per_day",
        "max_probation_position_changes_per_week",
        "max_probation_new_position_weight",
        "safe_mode_on_stale_data",
        "paper_execution_enabled_after_promotion",
        "real_broker_integration_enabled",
    }
)


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


def _load_portfolio_evolution_overrides(
    policy_path: Path,
    base_raw: Mapping[str, Any],
) -> tuple[Dict[str, float], Dict[str, Any]]:
    if not policy_path.exists():
        return {}, {"status": "NOT_CONFIGURED", "applied": False}
    policy = json.loads(policy_path.read_text(encoding="utf-8"))
    if policy.get("schema_version") != "brace-portfolio-evolution-policy-v1":
        raise ValueError("Unsupported BRACE portfolio evolution policy schema")
    if policy.get("never_apply_to_real_broker") is not True:
        raise ValueError("Portfolio evolution must explicitly prohibit real-broker use")
    expected_base_hash = str(policy.get("base_config_sha256") or "")
    actual_base_hash = _canonical_sha256(base_raw)
    if expected_base_hash != actual_base_hash:
        raise ValueError("Portfolio evolution policy was trained against a different base configuration")

    stored_hash = str(policy.get("content_sha256") or "")
    if stored_hash:
        hash_payload = dict(policy)
        hash_payload.pop("content_sha256", None)
        if stored_hash != _canonical_sha256(hash_payload):
            raise ValueError("Portfolio evolution policy integrity hash mismatch")

    overrides = policy.get("active_overrides") or {}
    unknown = set(overrides) - set(PORTFOLIO_EVOLUTION_FIELDS)
    if unknown:
        raise ValueError(
            f"Portfolio evolution contains non-whitelisted fields: {sorted(unknown)}"
        )
    if set(overrides) & IMMUTABLE_SAFETY_FIELDS:
        raise ValueError("Portfolio evolution attempted to modify the immutable Safety Kernel")

    clean: Dict[str, float] = {}
    for name, value in overrides.items():
        number = float(value)
        low, high = PORTFOLIO_EVOLUTION_FIELDS[name]
        if not low <= number <= high:
            raise ValueError(f"Portfolio evolution value for {name} is outside governed bounds")
        clean[name] = number
    return clean, {
        "status": str(policy.get("status") or "ACTIVE_PORTFOLIO_POLICY"),
        "applied": bool(clean),
        "content_sha256": policy.get("content_sha256"),
        "generated_at": policy.get("generated_at"),
        "scope": "BRACE portfolio construction; paper/shadow authority only",
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
    # Portfolio evolution defaults preserve current behaviour until evidence promotes
    # a challenger policy.
    portfolio_risk_aversion: float = 0.55
    portfolio_drawdown_penalty: float = 0.35
    portfolio_turnover_penalty: float = 0.35
    portfolio_diversification_penalty: float = 0.10
    portfolio_cash_floor: float = 0.0
    portfolio_rebalance_threshold: float = 0.01
    portfolio_max_active_positions: int = 12

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "EngineConfig":
        policy = raw.get("policy") if "policy" in raw else raw
        values: Dict[str, Any] = {}
        for name, field in cls.__dataclass_fields__.items():
            if name in policy:
                values[name] = policy[name]
            elif field.default is not MISSING:
                values[name] = field.default
            else:
                raise KeyError(f"Missing required BRACE policy field: {name}")
        values["portfolio_max_active_positions"] = int(
            round(float(values["portfolio_max_active_positions"]))
        )
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
        if not 0.20 <= self.portfolio_risk_aversion <= 0.90:
            raise ValueError("portfolio_risk_aversion outside governed bounds")
        if not 0.20 <= self.portfolio_drawdown_penalty <= 1.25:
            raise ValueError("portfolio_drawdown_penalty outside governed bounds")
        if not 0.10 <= self.portfolio_turnover_penalty <= 0.60:
            raise ValueError("portfolio_turnover_penalty outside governed bounds")
        if not 0.0 <= self.portfolio_diversification_penalty <= 0.40:
            raise ValueError("portfolio_diversification_penalty outside governed bounds")
        if not 0.0 <= self.portfolio_cash_floor <= 0.50:
            raise ValueError("portfolio_cash_floor outside governed bounds")
        if not 0.005 <= self.portfolio_rebalance_threshold <= 0.05:
            raise ValueError("portfolio_rebalance_threshold outside governed bounds")
        if not 2 <= self.portfolio_max_active_positions <= self.max_positions:
            raise ValueError(
                "portfolio_max_active_positions must stay within hard max_positions"
            )


def load_config(
    path: Path = DEFAULT_CONFIG_PATH,
    adaptive_path: Path | None = None,
    portfolio_evolution_path: Path | None = None,
) -> tuple[EngineConfig, Dict[str, Any]]:
    base_raw = json.loads(path.read_text(encoding="utf-8"))
    merged = deepcopy(base_raw)
    use_adaptive = path.resolve() == DEFAULT_CONFIG_PATH.resolve()
    if adaptive_path is not None or portfolio_evolution_path is not None:
        use_adaptive = True

    adaptive_metadata: Dict[str, Any] = {
        "status": "DISABLED_FOR_NONDEFAULT_CONFIG",
        "applied": False,
    }
    portfolio_metadata: Dict[str, Any] = {
        "status": "DISABLED_FOR_NONDEFAULT_CONFIG",
        "applied": False,
    }
    if use_adaptive:
        selected_path = adaptive_path or DEFAULT_ADAPTIVE_POLICY_PATH
        overrides, adaptive_metadata = _load_adaptive_overrides(selected_path, base_raw)
        merged.setdefault("policy", {}).update(overrides)

        selected_portfolio_path = (
            portfolio_evolution_path or DEFAULT_PORTFOLIO_EVOLUTION_POLICY_PATH
        )
        portfolio_overrides, portfolio_metadata = _load_portfolio_evolution_overrides(
            selected_portfolio_path,
            base_raw,
        )
        merged.setdefault("policy", {}).update(portfolio_overrides)

    merged["adaptive_policy_runtime"] = adaptive_metadata
    merged["portfolio_evolution_runtime"] = portfolio_metadata
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
        "portfolio_risk_aversion": config.portfolio_risk_aversion,
        "portfolio_drawdown_penalty": config.portfolio_drawdown_penalty,
        "portfolio_turnover_penalty": config.portfolio_turnover_penalty,
        "portfolio_diversification_penalty": config.portfolio_diversification_penalty,
        "portfolio_cash_floor": config.portfolio_cash_floor,
        "portfolio_rebalance_threshold": config.portfolio_rebalance_threshold,
        "portfolio_max_active_positions": config.portfolio_max_active_positions,
        "paper_execution_only": True,
        "real_broker_integration": False,
    }
