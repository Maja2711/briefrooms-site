# BriefRooms Investing Method v1.2.0

Dynamic ATR-based scenario thresholds.

Summary:

- ATR(14) is calculated from daily OHLC data.
- Expected weekly move = ATR(14) × sqrt(5).
- Upper model threshold = 0.9 × expected weekly move.
- Lower model threshold = 0.6 × expected weekly move.
- Values are converted into pips for EUR/USD and points for S&P 500 futures.
- Per-instrument minimum and maximum limits are applied.
- Static values remain only as a fallback if ATR data are unavailable.

Operational files:

- data/investments/methodology.json
- scripts/investments_thresholds.py
- scripts/investments_monitor.py
