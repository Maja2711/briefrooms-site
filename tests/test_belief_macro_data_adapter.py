from __future__ import annotations

import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from belief_macro_data_adapter import BLS_API, BLS_SERIES, MacroDataAdapter, parse_bls_series

UTC = timezone.utc


def _series(series_id: str, values):
    rows = []
    for year, month, value in values:
        rows.append({"year": str(year), "period": f"M{month:02d}", "value": str(value)})
    return {"seriesID": series_id, "data": list(reversed(rows))}


def sample_payload():
    cpi = []
    value = 300.0
    for month in range(7, 13):
        cpi.append((2025, month, value))
        value += 0.45
    for month in range(1, 8):
        cpi.append((2026, month, value))
        value += 0.95

    payroll = [
        (2026, 4, 159000),
        (2026, 5, 159030),
        (2026, 6, 159050),
        (2026, 7, 159060),
    ]
    unemployment = [
        (2026, 4, 4.0),
        (2026, 5, 4.1),
        (2026, 6, 4.2),
        (2026, 7, 4.4),
    ]
    return {
        "status": "REQUEST_SUCCEEDED",
        "Results": {
            "series": [
                _series(BLS_SERIES["cpi_sa"], cpi),
                _series(BLS_SERIES["payrolls"], payroll),
                _series(BLS_SERIES["unemployment"], unemployment),
            ]
        },
    }


class _FakeClient:
    def fetch(self, series_ids, *, start_year, end_year):
        self.series_ids = tuple(series_ids)
        self.start_year = start_year
        self.end_year = end_year
        return sample_payload()


class MacroDataAdapterTest(unittest.TestCase):
    def test_parser_orders_months_and_ignores_bad_rows(self):
        payload = sample_payload()
        parsed = parse_bls_series(payload)
        self.assertEqual(parsed[BLS_SERIES["payrolls"]][-1].period, "2026-M07")
        self.assertEqual(parsed[BLS_SERIES["unemployment"]][-1].value, 4.4)

    def test_bls_macro_data_creates_independent_inflation_and_labor_evidence(self):
        now = datetime(2026, 8, 18, 13, 0, tzinfo=UTC)
        adapter = MacroDataAdapter(client=_FakeClient())
        result = adapter.run(now)

        self.assertEqual(result.adapter, "macro_data")
        self.assertGreaterEqual(len(result.observations), 5)
        by_belief = {row.belief_id: row for row in result.evidence}
        self.assertIn("spx.financial_conditions.supportive", by_belief)
        self.assertIn("spx.trend.bullish", by_belief)

        inflation = by_belief["spx.financial_conditions.supportive"]
        labor = by_belief["spx.trend.bullish"]
        self.assertEqual(inflation.direction, -1)
        self.assertEqual(labor.direction, -1)
        self.assertNotEqual(inflation.independence_cluster, labor.independence_cluster)
        self.assertEqual(inflation.metadata["primary_source_ref"], BLS_API)
        self.assertEqual(labor.metadata["primary_source_ref"], BLS_API)
        self.assertTrue(inflation.metadata["primary_observation_ids"])
        self.assertEqual(len(labor.metadata["primary_observation_ids"]), 2)

    def test_old_monthly_data_is_observed_but_not_promoted_to_evidence(self):
        now = datetime(2026, 12, 20, 13, 0, tzinfo=UTC)
        adapter = MacroDataAdapter(client=_FakeClient())
        result = adapter.run(now)
        self.assertGreater(len(result.observations), 0)
        self.assertEqual(result.evidence, ())
        self.assertTrue(all(row.status == "stale" for row in result.observations))


if __name__ == "__main__":
    unittest.main()
