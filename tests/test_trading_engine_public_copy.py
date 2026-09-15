from __future__ import annotations

import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class TradingEnginePublicCopyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        subprocess.run(
            ['python', str(ROOT / 'scripts' / 'render_weekly_public_pages.py')],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )

    def read(self, path: str) -> str:
        return (ROOT / path).read_text(encoding='utf-8')

    def test_daily_and_stock_use_trading_engine_brand(self) -> None:
        for path in (
            'pl/inwestycje/daily-trading.html',
            'en/investing/daily-trading.html',
            'pl/inwestycje/stock-trading.html',
            'en/investing/stock-trading.html',
        ):
            source = self.read(path)
            self.assertIn('BriefRooms Trading Engine', source, path)
            self.assertNotIn('paper-trading', source.lower(), path)
            self.assertNotIn('paper trading', source.lower(), path)

    def test_weekly_generator_and_all_public_pages_use_trading_engine_brand(self) -> None:
        for path in (
            'pl/inwestycje/pozycje-tygodniowe.html',
            'pl/inwestycje/prognozy-tygodniowe.html',
            'en/investing/open-weekly-positions.html',
            'en/investing/weekly-forecasts.html',
        ):
            source = self.read(path)
            self.assertIn('BriefRooms Trading Engine · EUR/USD', source, path)
            self.assertIn('/scripts/investments-weekly-public-copy.js?v=1', source, path)

    def test_future_recovery_rationale_uses_public_brand_without_changing_internal_status(self) -> None:
        source = self.read('scripts/ensure_current_week_forecast.py')
        self.assertIn('Warstwa ciągłej ekspozycji jest częścią BriefRooms Trading Engine.', source)
        self.assertIn('The continuous-exposure layer is part of BriefRooms Trading Engine.', source)
        self.assertNotIn('pozostaje wyłącznie eksperymentem paper-trading', source)
        self.assertNotIn('remains an experimental paper-trading exercise only', source)
        self.assertIn('paper_trading_late_forecast_recovery', source)

    def test_current_week_public_copy_sanitizer_is_narrow_and_syntax_valid(self) -> None:
        script = ROOT / 'scripts' / 'investments-weekly-public-copy.js'
        completed = subprocess.run(
            ['node', '--check', str(script)],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        source = script.read_text(encoding='utf-8')
        self.assertIn('Warstwa ciągłej ekspozycji pozostaje wyłącznie eksperymentem paper-trading.', source)
        self.assertIn('Warstwa ciągłej ekspozycji jest częścią BriefRooms Trading Engine.', source)
        self.assertIn('The continuous-exposure layer remains an experimental paper-trading exercise only.', source)
        self.assertIn('The continuous-exposure layer is part of BriefRooms Trading Engine.', source)
        self.assertNotIn('innerHTML', source)


if __name__ == '__main__':
    unittest.main()
