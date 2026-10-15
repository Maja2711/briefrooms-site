from __future__ import annotations

import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from belief_macro_calendar_adapter import (
    ECB_CALENDAR,
    EUROSTAT_RELEASE_CALENDAR,
    MacroEventCalendarAdapter,
    calendar_event_to_observation,
    event_risk_evidence,
    parse_eu_calendar_text,
)

UTC = timezone.utc
BRUSSELS = ZoneInfo("Europe/Brussels")


class EuropeanCalendarRegressionTests(unittest.TestCase):
    def test_ecb_live_dd_mm_yyyy_decision_window(self):
        page = """<html><body>
        <h1>Schedules for the meetings of the Governing Council</h1>
        <div>28/10/2026</div><div>Governing Council of the ECB:
          monetary policy meeting in Frankfurt (Day 1)</div>
        <div>29/10/2026</div><div>Governing Council of the ECB:
          monetary policy meeting in Frankfurt (Day 2), followed by press conference</div>
        <div>29/10/2026</div><div>Press conference following the Governing Council meeting of the ECB</div>
        <div>25/11/2026</div><div>Governing Council of the ECB:
          non-monetary policy meeting (in Frankfurt)</div>
        </body></html>"""
        rows = parse_eu_calendar_text(
            page, now=datetime(2026, 10, 15, tzinfo=UTC),
            source="European Central Bank", source_ref=ECB_CALENDAR,
        )
        self.assertEqual(len(rows), 3)
        self.assertEqual({r.event_at.date().isoformat() for r in rows},
                         {"2026-10-28", "2026-10-29"})
        self.assertTrue(all(r.importance == "high" for r in rows))
        self.assertTrue(all(r.time_precision == "date_only" for r in rows))
        self.assertTrue(all(r.metadata["region"] == "EU" for r in rows))

    def test_eurostat_live_cards_have_title_before_release_date(self):
        page = """<html><body>
        <h3>Inflation (HICP), September 2026</h3>
        <p>15 October 2026 Data release Euro indicators release</p>
        <h3>Preliminary flash estimate GDP - EU and euro area, Q3/2026</h3>
        <p>29 October 2026 Data release Euro indicators release</p>
        <h3>Unemployment, September 2026</h3>
        <p>29 October 2026 Data release Euro indicators release</p>
        </body></html>"""
        rows = parse_eu_calendar_text(
            page, now=datetime(2026, 10, 15, tzinfo=UTC),
            source="Eurostat", source_ref=EUROSTAT_RELEASE_CALENDAR,
        )
        self.assertEqual(len(rows), 3)
        self.assertTrue(any("Inflation (HICP)" in r.title for r in rows))
        self.assertTrue(any("GDP" in r.title for r in rows))
        self.assertTrue(any("Unemployment" in r.title for r in rows))

    def test_eurostat_ics_subscription_handles_luxembourg_and_utc(self):
        feed = r"""BEGIN:VCALENDAR
VERSION:2.0
BEGIN:VEVENT
UID:hicp-20261015
SUMMARY:Inflation (HICP)\, September 2026
DTSTART;TZID=Europe/Luxembourg:20261015T110000
END:VEVENT
BEGIN:VEVENT
UID:gdp-20261029
SUMMARY:Preliminary flash estimate GDP, Q3 2026
DTSTART:20261029T100000Z
END:VEVENT
BEGIN:VEVENT
UID:cancelled
SUMMARY:Unemployment September 2026
DTSTART:20261030
STATUS:CANCELLED
END:VEVENT
END:VCALENDAR"""
        rows = parse_eu_calendar_text(
            feed, now=datetime(2026, 10, 15, 7, tzinfo=UTC),
            source="Eurostat", source_ref=EUROSTAT_RELEASE_CALENDAR,
        )
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0].event_at.astimezone(UTC).hour, 9)
        self.assertEqual(rows[0].time_precision, "exact")
        self.assertEqual(rows[1].event_at.astimezone(UTC).hour, 10)

    def test_eu_date_only_risk_checks_brussels_calendar_day(self):
        rows = parse_eu_calendar_text(
            "29/10/2026 Governing Council of the ECB: monetary policy meeting (Day 2)",
            now=datetime(2026, 10, 29, 11, tzinfo=UTC),
            source="European Central Bank", source_ref=ECB_CALENDAR,
        )
        self.assertEqual(len(rows), 1)
        obs = calendar_event_to_observation(rows[0], datetime(2026, 10, 29, 11, tzinfo=UTC))
        self.assertTrue(event_risk_evidence(obs, now=datetime(2026, 10, 29, 11, tzinfo=UTC)))

    def test_official_ics_is_connected_and_calendar_is_complete(self):
        self.assertEqual(
            EUROSTAT_RELEASE_CALENDAR,
            "https://ec.europa.eu/eurostat/o/calendars/eventsIcal?theme=&category=",
        )
        now = datetime(2026, 10, 15, 12, tzinfo=UTC)
        ics = """BEGIN:VCALENDAR
PRODID:-//Eurostat//Release calendar//EN
VERSION:2.0
BEGIN:VTIMEZONE
TZID:Europe/Luxembourg
END:VTIMEZONE
BEGIN:VEVENT
DTSTAMP:20261015T170125Z
DTSTART;VALUE=DATE:20261029
SUMMARY:Preliminary flash estimate GDP - EU and euro area
X-CATEGORY:Data release\\,Euro indicators release
UID:eurostat-gdp-20261029
END:VEVENT
BEGIN:VEVENT
DTSTAMP:20261015T170125Z
DTSTART;TZID=Europe/Luxembourg:20261030T110000
SUMMARY:Flash estimate inflation euro area
X-CATEGORY:Euro indicators release
UID:eurostat-hicp-20261030
END:VEVENT
END:VCALENDAR"""

        class FakeClient:
            def text(self, url):
                if url == EUROSTAT_RELEASE_CALENDAR:
                    return ics
                if "ecb.europa.eu" in url:
                    return ("28/10/2026 Governing Council of the ECB: "
                            "monetary policy meeting in Frankfurt (Day 1)")
                return ""

        audit = MacroEventCalendarAdapter(client=FakeClient()).run(now)
        coverage = next(row for row in audit.observations if row.metric == "calendar_coverage")
        self.assertTrue(coverage.metadata["complete"])
        self.assertEqual(coverage.metadata["sources"]["EUROSTAT"]["status"], "ok")
        self.assertEqual(coverage.metadata["sources"]["EUROSTAT"]["event_count"], 2)
        self.assertEqual(coverage.metadata["sources"]["ECB"]["event_count"], 1)
        calendar_events = [row for row in audit.observations if row.metric == "scheduled_macro_event"]
        self.assertTrue(any("flash estimate gdp" in row.metadata.get("title", "").lower()
                            for row in calendar_events))

    def test_empty_eurostat_is_not_a_green_calendar(self):
        now = datetime(2026, 10, 15, 12, tzinfo=UTC)

        class FakeClient:
            def text(self, url):
                if "ecb.europa.eu" in url:
                    return ("28/10/2026 Governing Council of the ECB: "
                            "monetary policy meeting in Frankfurt (Day 1)")
                if "eurostat" in url:
                    return "<html><h1>Release calendar</h1><div>Want to subscribe?</div></html>"
                return ""

        result = MacroEventCalendarAdapter(client=FakeClient()).run(now)
        audit = next(r for r in result.observations if r.metric == "calendar_coverage")
        self.assertIs(audit.metadata["complete"], False)
        self.assertEqual(audit.metadata["sources"]["EUROSTAT"]["status"], "empty_calendar")
        self.assertEqual(audit.metadata["sources"]["EUROSTAT"]["event_count"], 0)
        self.assertEqual(audit.metadata["sources"]["ECB"]["status"], "ok")


if __name__ == "__main__":
    unittest.main()
