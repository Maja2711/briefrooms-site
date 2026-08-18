from __future__ import annotations

import json
import math
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import List, Mapping, Optional, Sequence
from zoneinfo import ZoneInfo

from belief_adapter_contract import AdapterResult, Observation
from belief_core import iso_z

NY = ZoneInfo("America/New_York")
YAHOO_BASE = "https://query1.finance.yahoo.com/v8/finance/chart"
USER_AGENT = "BriefRooms-BeliefCore/2.1 (+shadow-research)"
DEFAULT_SYMBOLS = ("SPY", "RSP", "IWM", "^VIX", "HYG", "LQD", "TLT", "UUP")


@dataclass(frozen=True)
class Bar:
    timestamp: datetime
    close: float
    open: Optional[float] = None
    high: Optional[float] = None
    low: Optional[float] = None
    volume: Optional[float] = None


class YahooChartClient:
    def __init__(self, timeout: int = 15) -> None:
        self.timeout = timeout

    def bars(self, symbol: str, range_: str = "10d", interval: str = "30m") -> List[Bar]:
        encoded = urllib.parse.quote(symbol, safe="")
        url = f"{YAHOO_BASE}/{encoded}?range={range_}&interval={interval}&includePrePost=false&events=div%2Csplits"
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            payload = json.load(resp)
        result = ((payload.get("chart") or {}).get("result") or [None])[0]
        if not result:
            raise RuntimeError(f"Yahoo returned no chart result for {symbol}")
        timestamps = result.get("timestamp") or []
        quote = (((result.get("indicators") or {}).get("quote") or [{}])[0])
        opens = quote.get("open") or []
        highs = quote.get("high") or []
        lows = quote.get("low") or []
        closes = quote.get("close") or []
        volumes = quote.get("volume") or []
        out: List[Bar] = []
        for idx, epoch in enumerate(timestamps):
            close = closes[idx] if idx < len(closes) else None
            if close is None:
                continue
            def opt(rows: Sequence[object]) -> Optional[float]:
                if idx >= len(rows) or rows[idx] is None:
                    return None
                return float(rows[idx])
            out.append(Bar(
                timestamp=datetime.fromtimestamp(int(epoch), tz=timezone.utc),
                close=float(close),
                open=opt(opens), high=opt(highs), low=opt(lows), volume=opt(volumes),
            ))
        if not out:
            raise RuntimeError(f"Yahoo returned no usable bars for {symbol}")
        return out


class MarketSnapshot:
    def __init__(self, bars: Mapping[str, Sequence[Bar]]) -> None:
        self.bars = {key: list(value) for key, value in bars.items()}

    def latest(self, symbol: str) -> float:
        return self.bars[symbol][-1].close

    def observed_at(self, symbol: str = "SPY") -> datetime:
        return self.bars[symbol][-1].timestamp

    def return_over_bars(self, symbol: str, n: int) -> float:
        rows = self.bars[symbol]
        if len(rows) <= n:
            return 0.0
        old, new = rows[-1 - n].close, rows[-1].close
        return 0.0 if old == 0 else new / old - 1.0

    def ratio_return(self, numerator: str, denominator: str, n: int) -> float:
        a, b = self.bars[numerator], self.bars[denominator]
        m = min(len(a), len(b))
        if m <= n:
            return 0.0
        r0 = a[-1 - n].close / b[-1 - n].close
        r1 = a[-1].close / b[-1].close
        return 0.0 if r0 == 0 else r1 / r0 - 1.0

    def ratio(self, numerator: str, denominator: str) -> float:
        return self.latest(numerator) / self.latest(denominator)

    def is_current_session(self, now: datetime) -> bool:
        return self.observed_at("SPY").astimezone(NY).date() == now.astimezone(NY).date()

    def session_bars(self, symbol: str, session_date=None) -> List[Bar]:
        day = session_date or self.observed_at(symbol).astimezone(NY).date()
        return [x for x in self.bars[symbol] if x.timestamp.astimezone(NY).date() == day]

    def previous_session_close(self, symbol: str) -> Optional[float]:
        current = self.observed_at(symbol).astimezone(NY).date()
        older = [x for x in self.bars[symbol] if x.timestamp.astimezone(NY).date() < current]
        return older[-1].close if older else None


class MarketDataAdapter:
    name = "market_data"
    version = "1.0.0"

    def __init__(self, client: Optional[YahooChartClient] = None, symbols: Sequence[str] = DEFAULT_SYMBOLS) -> None:
        self.client = client or YahooChartClient()
        self.symbols = tuple(symbols)

    def fetch_snapshot(self) -> MarketSnapshot:
        return MarketSnapshot({symbol: self.client.bars(symbol, "10d", "30m") for symbol in self.symbols})

    def run(self, snapshot: MarketSnapshot) -> AdapterResult:
        observations: List[Observation] = []
        for symbol in self.symbols:
            rows = snapshot.bars[symbol]
            latest = rows[-1]
            observed_at = iso_z(latest.timestamp)
            source_ref = f"yahoo:{symbol}:{observed_at}"
            session = snapshot.session_bars(symbol)
            prev_close = snapshot.previous_session_close(symbol)
            session_open = next((x.open for x in session if x.open is not None), session[0].close if session else latest.close)
            session_highs = [x.high for x in session if x.high is not None]
            session_lows = [x.low for x in session if x.low is not None]
            volumes = [x.volume for x in session if x.volume is not None]
            session_volume = float(sum(volumes)) if volumes else None
            dollar_turnover = None if session_volume is None else sum(x.close * float(x.volume or 0.0) for x in session)
            gap = None if prev_close in (None, 0) else float(session_open) / float(prev_close) - 1.0
            change_1d = snapshot.return_over_bars(symbol, 13)
            rv = self._realized_vol(session)
            base = dict(adapter=self.name, entity=symbol, observed_at=observed_at,
                        source="Yahoo Finance chart", source_type="secondary", reliability=.82,
                        tags=("market", "ohlcv", self.version))
            def add(metric: str, value, unit: str, cluster: str, status: str = "ok", metadata=None) -> None:
                observations.append(Observation.make(**base, metric=metric, value=value, unit=unit,
                    source_ref=source_ref, independence_cluster=cluster, status=status, metadata=metadata))
            add("price", latest.close, "price", f"market:{symbol}:price")
            add("change_1d", change_1d, "return", f"market:{symbol}:price")
            add("session_open", session_open, "price", f"market:{symbol}:ohlc")
            add("session_high", max(session_highs) if session_highs else None, "price", f"market:{symbol}:ohlc", "ok" if session_highs else "unavailable")
            add("session_low", min(session_lows) if session_lows else None, "price", f"market:{symbol}:ohlc", "ok" if session_lows else "unavailable")
            add("session_volume", session_volume, "shares", f"market:{symbol}:volume", "ok" if session_volume is not None else "unavailable")
            add("dollar_turnover", dollar_turnover, "currency_notional", f"market:{symbol}:volume", "ok" if dollar_turnover is not None else "unavailable", {"calculation": "sum(close*bar_volume)"})
            add("gap", gap, "return", f"market:{symbol}:gap", "ok" if gap is not None else "unavailable")
            add("realized_volatility", rv, "return", f"market:{symbol}:volatility", "ok" if rv is not None else "unavailable", {"calculation": "sqrt(sum(log_return^2)) current session"})
            add("bid_ask_spread", None, "price", f"market:{symbol}:spread", "unavailable", {"reason": "Yahoo chart OHLCV does not expose executable bid/ask; v1 never fabricates spread"})
        return AdapterResult(self.name, tuple(observations), ())

    @staticmethod
    def _realized_vol(rows: Sequence[Bar]) -> Optional[float]:
        closes = [x.close for x in rows if x.close > 0]
        if len(closes) < 2:
            return None
        return math.sqrt(sum(math.log(b / a) ** 2 for a, b in zip(closes, closes[1:])))
