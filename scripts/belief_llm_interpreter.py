from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Optional, Sequence, Tuple

from belief_adapter_contract import EvidenceAssessment, Observation, clamp, observation_to_evidence
from belief_core import Evidence, iso_z

ALLOWED_BELIEFS: Tuple[str, ...] = (
    "spx.trend.bullish",
    "spx.breadth.healthy",
    "spx.volatility.benign",
    "spx.liquidity.supportive",
    "spx.financial_conditions.supportive",
)

MIN_INTERPRETATION_CONFIDENCE = 0.68
MIN_MATERIALITY = 0.55


@dataclass(frozen=True)
class Interpretation:
    belief_id: Optional[str]
    direction: Optional[int]
    strength: float
    confidence: float
    materiality: float
    event_type: str
    market_scope: str
    horizon_hours: int
    summary: str
    alternative_hypothesis: str
    model: str = ""


@dataclass(frozen=True)
class LLMInterpretationResult:
    observation: Observation
    evidence: Evidence
    interpretation: Interpretation


def _bounded_float(value: Any, field: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be numeric") from exc
    if not 0.0 <= number <= 1.0:
        raise ValueError(f"{field} must be in [0,1]")
    return number


def validate_interpretation_payload(
    payload: Mapping[str, Any],
    *,
    allowed_beliefs: Sequence[str] = ALLOWED_BELIEFS,
    model: str = "",
) -> Interpretation:
    if not isinstance(payload, Mapping):
        raise ValueError("LLM interpretation must be a JSON object")

    raw_belief = payload.get("belief_id")
    if raw_belief in (None, "", "none", "NONE", "null"):
        belief_id = None
    else:
        belief_id = str(raw_belief).strip()
        if belief_id not in set(allowed_beliefs):
            raise ValueError(f"unsupported belief_id: {belief_id}")

    raw_direction = payload.get("direction")
    direction: Optional[int]
    if belief_id is None:
        direction = None
    else:
        try:
            direction = int(raw_direction)
        except (TypeError, ValueError) as exc:
            raise ValueError("direction must be -1 or +1 when belief_id is set") from exc
        if direction not in (-1, 1):
            raise ValueError("direction must be -1 or +1")

    strength = _bounded_float(payload.get("strength", 0.0), "strength")
    confidence = _bounded_float(payload.get("confidence", 0.0), "confidence")
    materiality = _bounded_float(payload.get("materiality", 0.0), "materiality")
    event_type = str(payload.get("event_type") or "other").strip()[:80]
    market_scope = str(payload.get("market_scope") or "unknown").strip()[:80]
    summary = " ".join(str(payload.get("summary") or "").split())[:800]
    alternative = " ".join(str(payload.get("alternative_hypothesis") or "").split())[:800]
    try:
        horizon_hours = int(payload.get("horizon_hours") or 24)
    except (TypeError, ValueError) as exc:
        raise ValueError("horizon_hours must be an integer") from exc
    if not 1 <= horizon_hours <= 168:
        raise ValueError("horizon_hours must be between 1 and 168")

    if belief_id is not None and not summary:
        raise ValueError("directional interpretation requires a summary")

    return Interpretation(
        belief_id=belief_id,
        direction=direction,
        strength=strength,
        confidence=confidence,
        materiality=materiality,
        event_type=event_type,
        market_scope=market_scope,
        horizon_hours=horizon_hours,
        summary=summary,
        alternative_hypothesis=alternative,
        model=model,
    )


def interpretation_to_evidence(
    primary_observation: Observation,
    interpretation: Interpretation,
) -> Optional[LLMInterpretationResult]:
    if primary_observation.status != "ok":
        return None
    if interpretation.belief_id is None or interpretation.direction is None:
        return None
    if interpretation.confidence < MIN_INTERPRETATION_CONFIDENCE:
        return None
    if interpretation.materiality < MIN_MATERIALITY:
        return None

    effective_strength = clamp(
        interpretation.strength
        * (0.55 + 0.45 * interpretation.confidence)
        * (0.60 + 0.40 * interpretation.materiality)
    )
    if effective_strength < 0.08:
        return None

    interpreted = Observation.make(
        adapter="llm_event_interpreter",
        metric="event_interpretation",
        entity=primary_observation.entity,
        observed_at=primary_observation.observed_at,
        value={
            "belief_id": interpretation.belief_id,
            "direction": interpretation.direction,
            "strength": interpretation.strength,
            "confidence": interpretation.confidence,
            "materiality": interpretation.materiality,
            "event_type": interpretation.event_type,
            "market_scope": interpretation.market_scope,
            "horizon_hours": interpretation.horizon_hours,
        },
        unit="structured_interpretation",
        source=f"Gemini interpretation of {primary_observation.source}",
        source_type="derived",
        source_ref=f"derived:{primary_observation.observation_id}",
        reliability=primary_observation.reliability,
        independence_cluster=primary_observation.independence_cluster,
        tags=("llm_interpreted", "shadow", interpretation.event_type),
        metadata={
            "upstream_observation_id": primary_observation.observation_id,
            "upstream_source": primary_observation.source,
            "upstream_source_ref": primary_observation.source_ref,
            "llm_model": interpretation.model,
            "llm_confidence": interpretation.confidence,
            "materiality": interpretation.materiality,
            "market_scope": interpretation.market_scope,
            "horizon_hours": interpretation.horizon_hours,
            "summary": interpretation.summary,
            "alternative_hypothesis": interpretation.alternative_hypothesis,
        },
    )
    evidence = observation_to_evidence(
        interpreted,
        EvidenceAssessment(
            belief_id=interpretation.belief_id,
            direction=interpretation.direction,
            strength=effective_strength,
            evidence_type=f"event:{interpretation.event_type}",
            note=interpretation.summary,
            independence_cluster=primary_observation.independence_cluster,
            metadata={
                "primary_observation_id": primary_observation.observation_id,
                "primary_source_ref": primary_observation.source_ref,
                "llm_model": interpretation.model,
                "llm_confidence": interpretation.confidence,
                "materiality": interpretation.materiality,
                "market_scope": interpretation.market_scope,
                "horizon_hours": interpretation.horizon_hours,
                "alternative_hypothesis": interpretation.alternative_hypothesis,
            },
        ),
    )
    return LLMInterpretationResult(interpreted, evidence, interpretation)


class GeminiEvidenceInterpreter:
    """Fail-closed LLM interpreter.

    The primary-source Observation is always retained independently. Gemini may
    create a derived Observation and Evidence, but an invalid/weak response can
    never suppress or alter the source Observation.
    """

    def __init__(
        self,
        *,
        runtime: Any = None,
        request_json: Optional[Callable[[list[dict[str, str]], int, float], Mapping[str, Any]]] = None,
        allowed_beliefs: Sequence[str] = ALLOWED_BELIEFS,
    ) -> None:
        self.allowed_beliefs = tuple(allowed_beliefs)
        self._request_json = request_json
        self.runtime = runtime
        self._runtime_error = ""
        if runtime is None and request_json is None:
            try:
                import sitecustomize  # noqa: F401
                from comment_quality import get_ai_runtime
                self.runtime = get_ai_runtime()
            except Exception as exc:
                self._runtime_error = f"{type(exc).__name__}: {exc}"
                self.runtime = None

    @property
    def available(self) -> bool:
        if self._request_json is not None:
            return True
        runtime = self.runtime
        return bool(
            runtime
            and getattr(runtime, "available", False)
            and getattr(runtime, "provider", "") == "gemini"
        )

    @property
    def model(self) -> str:
        return str(getattr(self.runtime, "generation_model", "") or os.getenv("GEMINI_GENERATION_MODEL", ""))

    def _default_request(
        self,
        messages: list[dict[str, str]],
        max_tokens: int,
        temperature: float,
    ) -> Mapping[str, Any]:
        import requests
        import sitecustomize  # noqa: F401
        from comment_quality import request_json_completion

        return request_json_completion(
            post=requests.post,
            runtime=self.runtime,
            messages=messages,
            max_tokens=max_tokens,
            temperature=temperature,
            timeout=45,
        )

    def interpret(self, observation: Observation) -> Optional[LLMInterpretationResult]:
        if observation.status != "ok" or not self.available:
            return None

        document_text = str(observation.metadata.get("document_text") or observation.value or "")
        document_text = document_text.strip()
        if len(document_text) < 40:
            return None

        prompt = {
            "task": (
                "Interpret one already-observed primary-source market event. "
                "Do not invent facts, forecasts, prices, or consensus estimates. "
                "Choose at most one allowed belief that this event directly changes over the next 1-7 days. "
                "If the document is company-specific or immaterial to the shared US-market world state, return belief_id='none'."
            ),
            "allowed_beliefs": list(self.allowed_beliefs),
            "belief_meanings": {
                "spx.trend.bullish": "broad US equity/SPX trend bullish into the target horizon",
                "spx.breadth.healthy": "breadth/participation of the US equity advance is healthy",
                "spx.volatility.benign": "equity volatility remains contained",
                "spx.liquidity.supportive": "credit/liquidity conditions remain supportive",
                "spx.financial_conditions.supportive": "rates/USD/credit financial conditions remain supportive",
            },
            "hard_rules": [
                "Use only the supplied document.",
                "A company filing normally maps to none unless it plausibly has broad-market impact.",
                "direction=+1 supports the selected belief; direction=-1 contradicts it.",
                "confidence measures confidence in the interpretation, not source reliability.",
                "materiality measures broad-market relevance, not how dramatic the headline sounds.",
                "Return no directional belief when evidence is ambiguous.",
                "alternative_hypothesis must state the strongest plausible contrary reading.",
            ],
            "source": {
                "source": observation.source,
                "source_ref": observation.source_ref,
                "entity": observation.entity,
                "observed_at": observation.observed_at,
                "metric": observation.metric,
                "metadata": {
                    key: value for key, value in dict(observation.metadata).items()
                    if key not in {"document_text"}
                },
                "document_text": document_text[:16000],
            },
            "required_json": {
                "belief_id": "one allowed belief or 'none'",
                "direction": "1, -1, or 0 when none",
                "strength": "0..1",
                "confidence": "0..1",
                "materiality": "0..1",
                "event_type": "fed_policy|fed_speech|macro_release|earnings|guidance|sec_filing|regulatory|other",
                "market_scope": "broad_market|sector|single_company|macro|credit|rates|other",
                "horizon_hours": "integer 1..168",
                "summary": "one concise factual interpretation",
                "alternative_hypothesis": "strongest contrary reading",
            },
        }
        messages = [
            {
                "role": "system",
                "content": (
                    "You are a conservative evidence classifier inside an auditable market belief engine. "
                    "Your job is classification, not trading advice. Return strict JSON only."
                ),
            },
            {"role": "user", "content": json.dumps(prompt, ensure_ascii=False)},
        ]
        requester = self._request_json or self._default_request
        try:
            payload = requester(messages, 1800, 0.0)
            interpretation = validate_interpretation_payload(
                payload,
                allowed_beliefs=self.allowed_beliefs,
                model=self.model,
            )
        except Exception:
            return None
        return interpretation_to_evidence(observation, interpretation)
