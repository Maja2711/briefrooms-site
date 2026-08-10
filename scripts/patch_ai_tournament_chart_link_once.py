#!/usr/bin/env python3
from pathlib import Path

PATH = Path('scripts/ai-tournament-public.js')


def replace_once(text: str, old: str, new: str, label: str) -> str:
    if new in text:
        return text
    if old not in text:
        raise SystemExit(f'Expected snippet not found: {label}')
    return text.replace(old, new, 1)


def main() -> None:
    text = PATH.read_text(encoding='utf-8')

    text = replace_once(
        text,
        "currentRanking: 'Current ranking', currentReturns: 'Current returns', openFull: 'Open full AI Tournament →',",
        "currentRanking: 'Current ranking', currentReturns: 'Performance over time — line colors match the ranking', openFull: 'Open full AI Tournament →',",
        'EN chart subtitle',
    )
    text = replace_once(
        text,
        "currentRanking: 'Aktualny ranking', currentReturns: 'Aktualne wyniki', openFull: 'Otwórz pełny AI Tournament →',",
        "currentRanking: 'Aktualny ranking', currentReturns: 'Wynik w czasie — kolory linii odpowiadają rankingowi', openFull: 'Otwórz pełny AI Tournament →',",
        'PL chart subtitle',
    )
    text = replace_once(
        text,
        "const history = Array.isArray(data.history) ? data.history.slice(-14) : [];",
        "const history = Array.isArray(data.history) ? data.history.slice().sort((a, b) => String(a?.session_date || '').localeCompare(String(b?.session_date || ''))).slice(-14) : [];",
        'chronological history',
    )
    text = replace_once(
        text,
        "const width = 560, height = 205, left = 44, right = 12, top = 12, bottom = 34;",
        "const width = 640, height = 215, left = 44, right = 116, top = 14, bottom = 34;",
        'chart label gutter',
    )
    text = replace_once(
        text,
        "return `<div class=\"aitx-rank-row\"><span class=\"aitx-rank-number\">${esc(row.rank || '—')}</span>",
        "return `<div class=\"aitx-rank-row\" data-agent=\"${esc(row.agent_id)}\" tabindex=\"0\" style=\"--aitx-agent-color:${theme(row.agent_id).color}\"><span class=\"aitx-rank-number\">${esc(row.rank || '—')}</span>",
        'ranking identity',
    )

    css_old = ".aitx-chart{display:block;width:100%;height:auto;min-height:170px}.aitx-chart-grid{stroke:#e7edf4;stroke-width:1}.aitx-chart-axis{fill:#7a889a;font-size:10px;font-weight:700}.aitx-chart-line{fill:none;stroke-width:3;stroke-linecap:round;stroke-linejoin:round}.aitx-chart-dot{stroke:#fff;stroke-width:2}"
    css_new = ".aitx-chart{display:block;width:100%;height:auto;min-height:180px}.aitx-chart-grid{stroke:#e7edf4;stroke-width:1}.aitx-chart-axis{fill:#7a889a;font-size:10px;font-weight:700}.aitx-chart-line{fill:none;stroke-width:3;stroke-linecap:round;stroke-linejoin:round;transition:opacity .16s ease,stroke-width .16s ease}.aitx-chart-dot{stroke:#fff;stroke-width:2;transition:opacity .16s ease,r .16s ease}.aitx-chart-label{font-size:10px;font-weight:900;dominant-baseline:middle;paint-order:stroke;stroke:#fff;stroke-width:3px;stroke-linejoin:round;transition:opacity .16s ease}.aitx-chart-leader{stroke-width:1.25;opacity:.55;transition:opacity .16s ease}.aitx-rank-row[data-agent]{border-left:4px solid var(--aitx-agent-color);cursor:pointer;transition:opacity .16s ease,box-shadow .16s ease,transform .16s ease}.aitx-rank-row[data-agent]:hover,.aitx-rank-row[data-agent]:focus{outline:none;box-shadow:0 8px 22px rgba(28,47,73,.10);transform:translateY(-1px)}.aitx-shell.aitx-has-highlight .is-dimmed{opacity:.16!important}.aitx-shell.aitx-has-highlight .aitx-rank-row.is-dimmed{opacity:.38!important}.aitx-chart-line.is-highlighted{stroke-width:5}.aitx-chart-dot.is-highlighted{r:5}.aitx-chart-label.is-highlighted{font-size:11px}.aitx-rank-row.is-highlighted{box-shadow:0 8px 22px rgba(28,47,73,.12)}"
    if css_new not in text:
        if css_old not in text:
            raise SystemExit('Expected chart CSS block not found')
        text = text.replace(css_old, css_new, 1)

    if 'function installAgentLinking()' not in text:
        anchor = "  function installStyles() {\n"
        linking = r'''  function installAgentLinking() {
    if (window.__AITX_AGENT_LINKING__) return;
    window.__AITX_AGENT_LINKING__ = true;
    const interactive = '.aitx-rank-row[data-agent],.aitx-chart-line[data-agent],.aitx-chart-dot[data-agent],.aitx-chart-label[data-agent]';
    const setHighlight = (node, enabled) => {
      const shell = node?.closest?.('.aitx-shell');
      const agentId = node?.getAttribute?.('data-agent');
      if (!shell || !agentId) return;
      shell.classList.toggle('aitx-has-highlight', enabled);
      shell.querySelectorAll('[data-agent]').forEach(element => {
        const same = element.getAttribute('data-agent') === agentId;
        element.classList.toggle('is-highlighted', enabled && same);
        element.classList.toggle('is-dimmed', enabled && !same);
      });
    };
    document.addEventListener('pointerover', event => {
      const node = event.target?.closest?.(interactive);
      if (node) setHighlight(node, true);
    });
    document.addEventListener('pointerout', event => {
      const node = event.target?.closest?.(interactive);
      if (!node || (event.relatedTarget && node.contains(event.relatedTarget))) return;
      setHighlight(node, false);
    });
    document.addEventListener('focusin', event => {
      const node = event.target?.closest?.(interactive);
      if (node) setHighlight(node, true);
    });
    document.addEventListener('focusout', event => {
      const node = event.target?.closest?.(interactive);
      if (node) setHighlight(node, false);
    });
  }

'''
        if anchor not in text:
            raise SystemExit('installStyles anchor not found')
        text = text.replace(anchor, linking + anchor, 1)

    if '    installAgentLinking();\n  }\n\n  function icon(agentId) {' not in text:
        old = "    document.head.appendChild(style);\n  }\n\n  function icon(agentId) {"
        new = "    document.head.appendChild(style);\n    installAgentLinking();\n  }\n\n  function icon(agentId) {"
        if old not in text:
            raise SystemExit('installStyles end anchor not found')
        text = text.replace(old, new, 1)

    if 'const labelYByAgent = new Map' not in text:
        anchor = "    const lines = series.map(item => {\n"
        layout = r'''    const endpoints = series.map(item => {
      let lastIndex = -1;
      item.values.forEach((value, index) => { if (Number.isFinite(value)) lastIndex = index; });
      const lastValue = item.values[lastIndex];
      return Number.isFinite(lastValue) ? { agentId: item.agentId, lastValue, py: y(lastValue) } : null;
    }).filter(Boolean).sort((a, b) => a.py - b.py);
    const minLabelGap = 15;
    const minLabelY = top + 7;
    const maxLabelY = height - bottom - 7;
    endpoints.forEach((item, index) => {
      item.labelY = Math.max(item.py, index ? endpoints[index - 1].labelY + minLabelGap : minLabelY);
    });
    if (endpoints.length && endpoints[endpoints.length - 1].labelY > maxLabelY) {
      endpoints[endpoints.length - 1].labelY = maxLabelY;
      for (let index = endpoints.length - 2; index >= 0; index -= 1) {
        endpoints[index].labelY = Math.min(endpoints[index].labelY, endpoints[index + 1].labelY - minLabelGap);
      }
    }
    const labelYByAgent = new Map(endpoints.map(item => [item.agentId, Math.max(minLabelY, item.labelY)]));

'''
        if anchor not in text:
            raise SystemExit('Chart lines anchor not found')
        text = text.replace(anchor, layout + anchor, 1)

    if 'class=\"aitx-chart-label\" data-agent=' not in text:
        old = "      return `<polyline class=\"aitx-chart-line\" stroke=\"${color}\" points=\"${points}\"></polyline>${Number.isFinite(lastValue) ? `<circle class=\"aitx-chart-dot\" fill=\"${color}\" cx=\"${x(lastIndex)}\" cy=\"${y(lastValue)}\" r=\"4\"></circle>` : ''}`;"
        new = "      const agentId = esc(item.agentId);\n      const labelY = Number.isFinite(lastValue) ? (labelYByAgent.get(item.agentId) ?? y(lastValue)) : 0;\n      return `<polyline class=\"aitx-chart-line\" data-agent=\"${agentId}\" stroke=\"${color}\" points=\"${points}\"></polyline>${Number.isFinite(lastValue) ? `<circle class=\"aitx-chart-dot\" data-agent=\"${agentId}\" fill=\"${color}\" cx=\"${x(lastIndex)}\" cy=\"${y(lastValue)}\" r=\"4\"></circle><line class=\"aitx-chart-leader\" data-agent=\"${agentId}\" stroke=\"${color}\" x1=\"${x(lastIndex) + 5}\" y1=\"${y(lastValue)}\" x2=\"${x(lastIndex) + 13}\" y2=\"${labelY}\"></line><text class=\"aitx-chart-label\" data-agent=\"${agentId}\" fill=\"${color}\" x=\"${x(lastIndex) + 17}\" y=\"${labelY}\">${agentId}</text>` : ''}`;"
        if old not in text:
            raise SystemExit('Chart series block not found')
        text = text.replace(old, new, 1)

    PATH.write_text(text, encoding='utf-8')


if __name__ == '__main__':
    main()
