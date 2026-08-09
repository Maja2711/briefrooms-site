#!/usr/bin/env python3
"""Run multi-timeframe MA research with enough D1 history for MA200 on M1.

The underlying research module intentionally remains unchanged for auditability. This
runner extends only its daily-history request from 15y to max so a 200-month moving
average can exist before the recent H1/H4 common sample starts.
"""
from __future__ import annotations

import investments_weekly_eurusd_ma_multitimeframe_research as research

_original_download = research.yf.download


def download_with_monthly_depth(*args, **kwargs):
    if kwargs.get("interval") == "1d" and kwargs.get("period") == "15y":
        kwargs = dict(kwargs)
        kwargs["period"] = "max"
    return _original_download(*args, **kwargs)


research.yf.download = download_with_monthly_depth
research.main()
