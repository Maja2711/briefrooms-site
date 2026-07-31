from __future__ import annotations

import sys
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import brace_spx_signal_orthogonality_audit as audit


class SignalOrthogonalityAuditTests(unittest.TestCase):
    def synthetic(self) -> pd.DataFrame:
        rng = np.random.default_rng(42)
        index = pd.date_range("2010-01-01", periods=1200, freq="B")
        a = rng.normal(size=len(index))
        b = 0.85 * a + rng.normal(scale=0.35, size=len(index))
        c = rng.normal(size=len(index))
        d = 0.75 * c + rng.normal(scale=0.45, size=len(index))
        e = rng.normal(size=len(index))
        return pd.DataFrame({"trend": a, "breadth": b, "liquidity": c, "options": d, "rates": e}, index=index)

    def test_selection_is_capped_and_not_empty(self):
        frame = audit._standardize(self.synthetic())
        selected, excluded, unique = audit.select_sources(frame)
        self.assertGreaterEqual(len(selected), 2)
        self.assertLessEqual(len(selected), 4)
        self.assertEqual(set(selected) | set(excluded), set(audit.SOURCES))
        self.assertEqual(set(unique), set(audit.SOURCES))

    def test_residual_factors_are_orthogonal(self):
        frame = audit._standardize(self.synthetic())
        selected, _, _ = audit.select_sources(frame)
        factors = audit.orthogonal_residual_factors(frame, selected)
        correlation = factors.corr().abs().to_numpy()
        off_diagonal = correlation[np.triu_indices_from(correlation, k=1)]
        self.assertLess(float(off_diagonal.max()) if len(off_diagonal) else 0.0, 1e-10)

    def test_effective_rank_is_bounded(self):
        correlation = audit._standardize(self.synthetic()).corr()
        rank = audit.effective_rank(correlation)
        self.assertGreaterEqual(rank, 1.0)
        self.assertLessEqual(rank, 5.0)


if __name__ == "__main__":
    unittest.main()
