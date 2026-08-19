from pathlib import Path
import json
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]
PORTFOLIO = ROOT / 'data/portfolio10k/paper_portfolio.json'
SCRIPT = ROOT / 'scripts/portfolio-10k-executed-decisions-history-sync.js'
INSTALLER = ROOT / 'scripts/install_brace_execution_history_sync.py'


class BraceExecutionHistorySyncTests(unittest.TestCase):
    def test_current_history_contains_latest_rotation(self):
        portfolio = json.loads(PORTFOLIO.read_text(encoding='utf-8'))
        groups = {}
        for tx in portfolio.get('transactions', []):
            if tx.get('executed_at') and tx.get('side') in {'BUY', 'SELL'}:
                groups.setdefault(tx.get('order_id') or tx.get('transaction_id'), []).append(tx)
        latest = sorted(
            groups.values(),
            key=lambda rows: max(row.get('executed_at', '') for row in rows),
            reverse=True,
        )[0]
        sides = {row['side'] for row in latest}
        instruments = {row['instrument_id'] for row in latest}
        self.assertEqual(sides, {'BUY', 'SELL'})
        self.assertEqual(instruments, {'spgi', 'jpm'})

    def test_frontend_uses_append_only_transaction_history(self):
        source = SCRIPT.read_text(encoding='utf-8')
        self.assertIn("paper_portfolio.transactions", source)
        self.assertIn("completedExecutions(portfolio)", source)
        self.assertIn("REPLACE", source)
        self.assertNotIn("status === 'PAPER_EXECUTED'", source)

    def test_javascript_syntax(self):
        result = subprocess.run(['node', '--check', str(SCRIPT)], cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_installer_targets_both_locales(self):
        source = INSTALLER.read_text(encoding='utf-8')
        self.assertIn("pl/inwestycje/portfel-10k.html", source)
        self.assertIn("en/investing/portfolio-10k.html", source)


if __name__ == '__main__':
    unittest.main()
