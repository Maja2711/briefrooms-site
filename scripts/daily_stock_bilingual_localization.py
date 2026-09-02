#!/usr/bin/env python3
"""Display-only bilingual localization for BriefRooms Daily Stock selections.

The trading decision, ranking, prices and risk geometry remain untouched.
This layer only guarantees that:
- GPW content displayed on English pages is English,
- US Daily content displayed on Polish pages is Polish.

Gemini is used for faithful translation when available. A deterministic,
fact-preserving fallback is always written so the UI never falls back to the
wrong language when translation infrastructure is temporarily unavailable.
"""
from __future__ import annotations

import argparse
import json
from copy import deepcopy
from pathlib import Path
from typing import Any, Mapping

try:
    from comment_quality import get_ai_runtime, request_json_completion
except ModuleNotFoundError:
    from scripts.comment_quality import get_ai_runtime, request_json_completion

ROOT = Path(__file__).resolve().parents[1]
MARKETS = {
    "gpw": {
        "public": ROOT / "data/investments/gpw_daily_pick.json",
        "history": ROOT / "data/investments/gpw_daily_pick_history",
        "trade_decision": "TRANSAKCJA",
        "target": "en",
        "source": "pl",
    },
    "us": {
        "public": ROOT / "data/investments/us_daily_stock.json",
        "history": ROOT / "data/investments/us_daily_stock_history",
        "trade_decision": "TRADE",
        "target": "pl",
        "source": "en",
    },
}


def load_json(path: Path) -> Any:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def atomic_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _source_rows(selection: Mapping[str, Any]) -> list[dict[str, str]]:
    rows = []
    for row in (selection.get("sources") or [])[:8]:
        if not isinstance(row, Mapping):
            continue
        source_id = str(row.get("id") or "").strip()
        title = str(row.get("title") or "").strip()
        if not source_id:
            continue
        rows.append({
            "id": source_id,
            "publisher": str(row.get("publisher") or "").strip(),
            "title": title,
            "source_kind": str(row.get("source_kind") or "").strip(),
        })
    return rows


def _complete_existing(selection: Mapping[str, Any], target: str) -> dict[str, Any] | None:
    localized = (selection.get("localized") or {}).get(target)
    if not isinstance(localized, Mapping):
        return None
    if not str(localized.get("thesis") or "").strip() or not str(localized.get("why_now") or "").strip():
        return None
    source_ids = {row["id"] for row in _source_rows(selection)}
    summaries = localized.get("source_summaries") or {}
    if source_ids and not source_ids.issubset(set(summaries)):
        return None
    return dict(localized)


def fallback_localization(selection: Mapping[str, Any], market: str) -> dict[str, Any]:
    symbol = str(selection.get("ticker") or selection.get("symbol") or "the selected stock")
    name = str(selection.get("name") or symbol)
    sources = _source_rows(selection)
    if market == "gpw":
        thesis = f"{name} is the active GPW Daily Trade selection after the highest validated ranking among eligible Polish-market candidates."
        why = (
            "The selection combines relative momentum, market and sector context, liquidity, risk/reward, "
            "current-session confirmation and the strategy's historical evidence."
        )
        activation = "Enter only inside the stated entry zone and do not chase the price above its upper limit."
        summaries = {
            row["id"]: (
                f"Current market quote for {symbol}."
                if row.get("source_kind") == "market_quote"
                else f"Evidence from {row.get('publisher') or 'the cited source'} used in the selected setup."
            )
            for row in sources
        }
        return {
            "language": "en",
            "source_language": "pl",
            "translation_mode": "deterministic_fallback",
            "thesis": thesis,
            "why_now": why,
            "activation": activation,
            "source_summaries": summaries,
        }

    thesis = f"{name} jest aktywnym wyborem US Daily Stock po najwyższym zweryfikowanym rankingu wśród dopuszczonych kandydatów rynku USA."
    why = (
        "Wybór łączy momentum relatywne, kontekst rynku i sektora, płynność, relację zysku do ryzyka, "
        "potwierdzenie bieżącej sesji oraz historyczne wyniki strategii dla rynku USA."
    )
    activation = "Wchodź wyłącznie w podanej strefie wejścia i nie goń ceny powyżej jej górnej granicy."
    summaries = {
        row["id"]: (
            f"Bieżące kwotowanie rynkowe dla {symbol}."
            if row.get("source_kind") == "market_quote"
            else f"Źródło {row.get('publisher') or 'wskazane w analizie'} wykorzystane w ocenie wybranego setupu."
        )
        for row in sources
    }
    return {
        "language": "pl",
        "source_language": "en",
        "translation_mode": "deterministic_fallback",
        "thesis": thesis,
        "why_now": why,
        "activation": activation,
        "source_summaries": summaries,
    }


def ai_localization(selection: Mapping[str, Any], market: str) -> dict[str, Any] | None:
    runtime = get_ai_runtime()
    if runtime.provider != "gemini" or not runtime.available:
        return None

    target = "English" if market == "gpw" else "Polish"
    source = "Polish" if market == "gpw" else "English"
    sources = _source_rows(selection)
    prompt = {
        "task": f"Translate an already selected Daily Stock trade from {source} into {target} for BriefRooms. This is display-only localization.",
        "rules": [
            "Preserve the investment conclusion, facts, numbers, ticker, company and product names exactly.",
            "Do not add forecasts, opinions, recommendations or facts absent from the source text.",
            f"Write concise, natural {target} for a financially literate reader.",
            "Translate thesis, why_now and activation faithfully.",
            "For every supplied source id return one short translated summary based only on the source title.",
        ],
        "selection": {
            "symbol": selection.get("symbol"),
            "name": selection.get("name"),
            "thesis": selection.get("thesis"),
            "why_now": selection.get("why_now"),
            "activation": selection.get("activation"),
            "sources": sources,
        },
        "output_schema": {
            "thesis": f"{target} translation",
            "why_now": f"{target} translation",
            "activation": f"{target} translation",
            "sources": [{"id": "source-id", "summary": f"short {target} summary"}],
        },
    }

    import requests

    result = request_json_completion(
        post=requests.post,
        runtime=runtime,
        messages=[
            {"role": "system", "content": "You are a precise financial translator. Preserve meaning and never invent facts."},
            {"role": "user", "content": json.dumps(prompt, ensure_ascii=False)},
        ],
        max_tokens=1800,
        temperature=0.1,
        timeout=45,
    )
    thesis = str(result.get("thesis") or "").strip()[:900]
    why = str(result.get("why_now") or "").strip()[:700]
    activation = str(result.get("activation") or "").strip()[:450]
    if not thesis or not why:
        return None

    allowed = {row["id"] for row in sources}
    summaries: dict[str, str] = {}
    for row in result.get("sources") or []:
        if not isinstance(row, Mapping):
            continue
        source_id = str(row.get("id") or "")
        summary = str(row.get("summary") or "").strip()[:360]
        if source_id in allowed and summary:
            summaries[source_id] = summary
    if allowed and not allowed.issubset(set(summaries)):
        return None

    return {
        "language": "en" if market == "gpw" else "pl",
        "source_language": "pl" if market == "gpw" else "en",
        "translation_mode": "gemini_faithful_translation",
        "thesis": thesis,
        "why_now": why,
        "activation": activation,
        "source_summaries": summaries,
    }


def localize_selection(selection: Mapping[str, Any], market: str, *, allow_ai: bool = True) -> tuple[dict[str, Any], str]:
    cfg = MARKETS[market]
    target = str(cfg["target"])
    result = deepcopy(dict(selection))
    existing = _complete_existing(result, target)
    if existing is not None:
        return result, "existing"

    localized = None
    if allow_ai:
        try:
            localized = ai_localization(result, market)
        except Exception:
            localized = None
    mode = "ai" if localized is not None else "fallback"
    localized = localized or fallback_localization(result, market)
    result.setdefault("localized", {})[target] = localized
    return result, mode


def localize_payload(payload: Mapping[str, Any], market: str, *, allow_ai: bool = True) -> tuple[dict[str, Any], str]:
    cfg = MARKETS[market]
    result = deepcopy(dict(payload))
    if result.get("decision") != cfg["trade_decision"] or not isinstance(result.get("selection"), Mapping):
        return result, "not_trade"
    selection, mode = localize_selection(result["selection"], market, allow_ai=allow_ai)
    result["selection"] = selection
    target = str(cfg["target"])
    result.setdefault("data_quality", {})[f"{target}_localization"] = {
        "status": "ready",
        "mode": mode,
        "display_only": True,
    }
    return result, mode


def _sync_canonical_history(payload: Mapping[str, Any], market: str) -> None:
    cfg = MARKETS[market]
    position = payload.get("position") or {}
    source_date = str(position.get("source_history_date") or payload.get("date") or "")
    if not source_date:
        return
    path = Path(cfg["history"]) / f"{source_date}.json"
    canonical = load_json(path)
    if not isinstance(canonical, dict) or not isinstance(canonical.get("selection"), dict):
        return
    current_selection = payload.get("selection") or {}
    if str((canonical.get("selection") or {}).get("symbol") or "") != str(current_selection.get("symbol") or ""):
        return
    target = str(cfg["target"])
    localized = (current_selection.get("localized") or {}).get(target)
    if localized:
        canonical["selection"].setdefault("localized", {})[target] = deepcopy(localized)
        canonical.setdefault("data_quality", {})[f"{target}_localization"] = {
            "status": "ready",
            "display_only": True,
        }
        atomic_json(path, canonical)


def run(market: str, *, allow_ai: bool = True) -> dict[str, Any]:
    cfg = MARKETS[market]
    path = Path(cfg["public"])
    payload = load_json(path)
    if not isinstance(payload, dict):
        return {"status": "NO_PUBLIC_PAYLOAD", "market": market}
    localized, mode = localize_payload(payload, market, allow_ai=allow_ai)
    if mode == "not_trade":
        return {"status": "NO_ACTIVE_TRADE", "market": market}
    atomic_json(path, localized)
    _sync_canonical_history(localized, market)
    return {
        "status": "READY",
        "market": market,
        "target_language": cfg["target"],
        "mode": mode,
        "symbol": (localized.get("selection") or {}).get("symbol"),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--market", choices=sorted(MARKETS), required=True)
    parser.add_argument("--no-ai", action="store_true", help="Use deterministic localization fallback only.")
    args = parser.parse_args()
    print("DAILY_STOCK_LOCALIZATION", run(args.market, allow_ai=not args.no_ai))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
