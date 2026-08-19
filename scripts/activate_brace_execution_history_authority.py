from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SYNC = ROOT / 'scripts/portfolio-10k-executed-decisions-history-sync.js'
FINALIZER = ROOT / 'scripts/portfolio-10k-execution-finalizer.js'

sync = SYNC.read_text(encoding='utf-8')
flag = "  window.__BR_BRACE_EXECUTION_HISTORY_AUTHORITY__ = true;\n"
needle = "  'use strict';\n"
if '__BR_BRACE_EXECUTION_HISTORY_AUTHORITY__' not in sync:
    sync = sync.replace(needle, needle + '\n' + flag, 1)
SYNC.write_text(sync, encoding='utf-8')

finalizer = FINALIZER.read_text(encoding='utf-8')
function_line = "  function renderExecutedDecisions(orders, paperPortfolio, universe, pending, reports, decisionContext) {\n"
guard = "    if (window.__BR_BRACE_EXECUTION_HISTORY_AUTHORITY__) return false;\n"
if guard not in finalizer:
    if function_line not in finalizer:
        raise SystemExit('renderExecutedDecisions signature not found')
    finalizer = finalizer.replace(function_line, function_line + guard, 1)
FINALIZER.write_text(finalizer, encoding='utf-8')

assert '__BR_BRACE_EXECUTION_HISTORY_AUTHORITY__ = true' in SYNC.read_text(encoding='utf-8')
assert guard.strip() in FINALIZER.read_text(encoding='utf-8')
print('BRACE transaction-history authority activated.')
