from __future__ import annotations

from pathlib import Path
import sys

import pytest
import numpy as np

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import brace_historical_accelerator as accelerator


def lesson(code: str, correct: bool, credit: float = 1.0) -> accelerator.Lesson:
    observed = pd.Timestamp("2020-01-03")
    return accelerator.Lesson(
        code=code,
        symbol="TEST",
        observed_at=observed,
        outcome_at=observed + pd.Timedelta(days=28),
        horizon_weeks=4,
        direction=1,
        strength=1.0,
        quality=1.0,
        excess_return=0.05 if correct else -0.05,
        correct=correct,
        credit=credit,
    )


def test_reliable_signal_gets_multiplier_above_one():
    rows = [lesson("price_vs_ma200", True) for _ in range(20)]
    rows += [lesson("price_vs_ma200", False) for _ in range(4)]
    stats = accelerator.fit_reliability(rows, pd.Timestamp("2021-01-01"), 8.0, 0.20)
    assert stats["price_vs_ma200"]["active"] is True
    assert 1.0 < stats["price_vs_ma200"]["multiplier"] <= 1.20


def test_unreliable_signal_gets_multiplier_below_one():
    rows = [lesson("relative_strength_6m", False) for _ in range(20)]
    rows += [lesson("relative_strength_6m", True) for _ in range(4)]
    stats = accelerator.fit_reliability(rows, pd.Timestamp("2021-01-01"), 8.0, 0.20)
    assert stats["relative_strength_6m"]["active"] is True
    assert 0.80 <= stats["relative_strength_6m"]["multiplier"] < 1.0


def test_small_sample_remains_neutral():
    rows = [lesson("drawdown_52w", True) for _ in range(4)]
    stats = accelerator.fit_reliability(rows, pd.Timestamp("2021-01-01"), 8.0, 0.20)
    assert stats["drawdown_52w"]["active"] is False
    assert stats["drawdown_52w"]["multiplier"] == 1.0


def test_historical_seed_credit_is_bounded():
    stats = {
        "price_vs_ma200": {
            "success_credit": 80.0,
            "failure_credit": 20.0,
            "effective_samples": 100.0,
            "active": True,
            "multiplier": 1.1,
        }
    }
    events = accelerator.seed_events(stats, "unit-test")
    total_credit = sum(
        event["evidence_attribution"][0]["credit"]
        for event in events
    )
    assert round(total_credit, 6) == accelerator.HISTORICAL_CREDIT_CAP
    assert all(event["historical_seed"] for event in events)


def test_progress_requires_untouched_test_improvement_and_risk_control():
    validation = {"objective": 0.01}
    good_test = {"cagr": 0.004, "sharpe": 0.0, "max_drawdown": -0.005, "objective": 0.003, "calmar": 0.03}
    bad_risk = {**good_test, "max_drawdown": -0.02}
    no_progress = {"cagr": 0.0, "sharpe": 0.0, "max_drawdown": 0.0, "objective": 0.0, "calmar": 0.0}
    assert accelerator.significant_progress(validation, good_test) is True
    assert accelerator.significant_progress(validation, bad_risk) is False
    assert accelerator.significant_progress(validation, no_progress) is False


def history(symbol: str, *, periods: int = 240, start: str = "2020-01-03") -> pd.Series:
    dates = pd.date_range(start, periods=periods, freq="W-FRI")
    return pd.Series(np.linspace(100, 145, periods), index=dates, name=symbol)


def test_historical_downloader_retries_missing_V_without_fabricating_prices(monkeypatch, tmp_path):
    import yfinance as yf

    cache_paths = []
    monkeypatch.setattr(yf, "set_tz_cache_location", lambda p: cache_paths.append(Path(p)))
    monkeypatch.setenv("RUNNER_TEMP", str(tmp_path))
    calls = []
    slept = []

    def flaky(symbol, start):
        calls.append(symbol)
        assert cache_paths and cache_paths[-1].exists()
        if symbol == "V" and calls.count("V") == 1:
            raise RuntimeError("OperationalError: database is locked")
        if symbol == "V" and calls.count("V") == 2:
            return pd.Series(dtype=float)
        return history(symbol)

    result = accelerator.download_verified_history(
        ["VT", "VBR", "V", "V"], "1995-01-01",
        fetcher=flaky, sleeper=slept.append,
        now=pd.Timestamp("2024-07-01"),
    )
    assert list(result.columns) == ["VT", "VBR", "V"]
    assert result["V"].equals(history("V"))
    assert calls == ["VT", "VBR", "V", "V", "V"]
    assert slept == [2, 4]
    assert len(cache_paths) == 1 and not cache_paths[0].exists()
    assert accelerator.bt.weekly_prices(result).dropna(how="any").shape[0] == 240


def test_historical_downloader_never_silently_omits_missing_ticker(monkeypatch, tmp_path):
    import yfinance as yf

    monkeypatch.setattr(yf, "set_tz_cache_location", lambda p: None)
    monkeypatch.setenv("RUNNER_TEMP", str(tmp_path))
    calls = []

    def incomplete(symbol, start):
        calls.append(symbol)
        if symbol == "V":
            return pd.Series(dtype=float)
        return history(symbol)

    with pytest.raises(RuntimeError, match="BRACE_HISTORY_SOURCE_INCOMPLETE_NO_TRAINING.*V"):
        accelerator.download_verified_history(
            ["VT", "V"], "1995-01-01", fetcher=incomplete,
            sleeper=lambda _: None, now=pd.Timestamp("2024-07-01"),
        )
    assert calls.count("V") == 3


def test_historical_downloader_rejects_missing_common_history_without_fill(monkeypatch, tmp_path):
    import yfinance as yf

    monkeypatch.setattr(yf, "set_tz_cache_location", lambda p: None)
    monkeypatch.setenv("RUNNER_TEMP", str(tmp_path))
    early = history("VT", periods=240, start="2016-01-01")
    late = history("V", periods=240, start="2020-01-03")
    with pytest.raises(RuntimeError, match="BRACE_HISTORY_COMMON_CALENDAR_INCOMPLETE_NO_TRAINING"):
        accelerator.download_verified_history(
            ["VT", "V"], "1995-01-01",
            fetcher=lambda s, _: early if s == "VT" else late,
            sleeper=lambda _: None, now=pd.Timestamp("2024-07-01"),
        )


def test_historical_downloader_rejects_stale_and_short_history(monkeypatch, tmp_path):
    import yfinance as yf

    monkeypatch.setattr(yf, "set_tz_cache_location", lambda p: None)
    monkeypatch.setenv("RUNNER_TEMP", str(tmp_path))
    with pytest.raises(RuntimeError, match="STALE_SYMBOL_HISTORY"):
        accelerator.download_verified_history(
            ["VT"], "1995-01-01",
            fetcher=lambda s, _: history(s, start="2010-01-01"),
            sleeper=lambda _: None, now=pd.Timestamp("2024-07-01"),
        )
    with pytest.raises(RuntimeError, match="INSUFFICIENT_SYMBOL_HISTORY"):
        accelerator.download_verified_history(
            ["V"], "1995-01-01",
            fetcher=lambda s, _: history(s, periods=15, start="2024-04-05"),
            sleeper=lambda _: None, now=pd.Timestamp("2024-07-01"),
        )


def test_fetch_single_history_disables_multiticker_threads(monkeypatch):
    import yfinance as yf

    observed = {}

    def fake_download(symbol, **kwargs):
        observed.update({"symbol":symbol, **kwargs})
        return pd.DataFrame({"Close":[101.0, 102.0]},
                            index=pd.to_datetime(["2024-01-05","2024-01-08"]))

    monkeypatch.setattr(yf, "download", fake_download)
    sample = accelerator.fetch_single_symbol_history("V", "1995-01-01")
    assert sample.name == "V"
    assert sample.iloc[-1] == 102.0
    assert observed["symbol"] == "V"
    assert observed["threads"] is False
    assert observed["auto_adjust"] is True
    assert observed["timeout"] == 35


def test_historical_validation_failure_does_not_write_learning_state(monkeypatch, tmp_path):
    portfolio = {"positions":[],"benchmark":{"market_symbol":"FWIA.DE"}}
    monkeypatch.setattr(accelerator.bt, "build_proxy_target",
                        lambda _: (pd.Series({"VT":0.5, "V":0.5}), {}))
    monkeypatch.setattr(accelerator.bt, "historical_symbol", lambda symbol: "VT")
    monkeypatch.setattr(
        accelerator, "download_verified_history",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            RuntimeError("BRACE_HISTORY_SOURCE_INCOMPLETE_NO_TRAINING V")
        )
    )
    memory_path = tmp_path / "memory.json"
    original = '{"decisions":[{"decision_id":"immutable"}]}\n'
    memory_path.write_text(original)
    with pytest.raises(RuntimeError, match="NO_TRAINING"):
        accelerator.build_training(portfolio, "1995-01-01")
    assert memory_path.read_text() == original
