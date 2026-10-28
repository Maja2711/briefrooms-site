"""NYSE US cash-session calendar used only for Shadow liveness SLA.

The dates are transcribed from the official NYSE Group calendar (2026–2028):
https://www.nyse.com/trade/hours-calendars

No implied forecast collection on weekends, official full-day holidays, or
beyond the verified calendar horizon. NYSE may announce exceptional closures:
update CLOSED or EARLY_CLOSE from an official notice before those dates.
This calendar does not change trading/execution or historic forecasts.
"""
from datetime import date, time

CALENDAR_SOURCE = "NYSE_OFFICIAL_2026_2028"
VERIFIED_YEARS = frozenset({2026, 2027, 2028})
CLOSED = frozenset({
    # 2026
    "2026-01-01", "2026-01-19", "2026-02-16", "2026-04-03",
    "2026-05-25", "2026-06-19", "2026-07-03", "2026-09-07",
    "2026-11-26", "2026-12-25",
    # 2027
    "2027-01-01", "2027-01-18", "2027-02-15", "2027-03-26",
    "2027-05-31", "2027-06-18", "2027-07-05", "2027-09-06",
    "2027-11-25", "2027-12-24",
    # 2028 (no New Year's Day holiday observed by NYSE in 2028)
    "2028-01-17", "2028-02-21", "2028-04-14", "2028-05-29",
    "2028-06-19", "2028-07-04", "2028-09-04", "2028-11-23",
    "2028-12-25",
})
EARLY_CLOSE = frozenset({
    "2026-11-27", "2026-12-24",
    "2027-11-26",
    "2028-07-03", "2028-11-24",
})
# Exceptional exchange-wide closures can be recorded here with an official
# reference in the same change; never infer one from missing Yahoo candles.
EXCEPTIONAL_CLOSED = frozenset()


def session_for(day: date) -> dict:
    if day.year not in VERIFIED_YEARS:
        return {"calendar_status": "UNVERIFIED", "session_open": False,
                "holiday_closed": False, "early_close": False,
                "close_time": None, "source": CALENDAR_SOURCE}
    is_closed = (day.weekday() >= 5 or day.isoformat() in CLOSED
                 or day.isoformat() in EXCEPTIONAL_CLOSED)
    short = not is_closed and day.isoformat() in EARLY_CLOSE
    return {
        "calendar_status": "VERIFIED", "session_open": not is_closed,
        "holiday_closed": is_closed and day.weekday() < 5,
        "early_close": short,
        "close_time": time(13, 0) if short else (time(16, 0) if not is_closed else None),
        "source": CALENDAR_SOURCE,
    }
