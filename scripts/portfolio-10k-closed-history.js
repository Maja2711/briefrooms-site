(() => {
  'use strict';

  const lang = (window.BR_PORTFOLIO_10K?.lang || document.documentElement.lang)
    .toLowerCase()
    .startsWith('en') ? 'en' : 'pl';
  const isPl = lang === 'pl';
  const locale = isPl ? 'pl-PL' : 'en-US';
  const reportCurrency = isPl ? 'PLN' : 'USD';

  const T = isPl ? {
    title: 'Zamknięte transakcje',
    subtitle: 'Pozycje sprzedane wraz z datą zakupu, datą sprzedaży i ostatecznym wynikiem po kosztach.',
    instrument: 'Instrument',
    purchaseDate: 'Data zakupu',
    purchasePrice: 'Cena zakupu',
    saleDate: 'Data sprzedaży',
    salePrice: 'Cena sprzedaży',
    units: 'Jednostki',
    purchaseCapital: 'Kapitał zakupu',
    saleValue: 'Wartość sprzedaży',
    result: 'Zysk / strata',
    status: 'Status',
    sold: 'sprzedana'
  } : {
    title: 'Closed transactions',
    subtitle: 'Sold positions with purchase date, sale date and final after-cost result.',
    instrument: 'Instrument',
    purchaseDate: 'Purchase date',
    purchasePrice: 'Purchase price',
    saleDate: 'Sale date',
    salePrice: 'Sale price',
    units: 'Units',
    purchaseCapital: 'Purchase capital',
    saleValue: 'Sale proceeds',
    result: 'Profit / loss',
    status: 'Status',
    sold: 'sold'
  };

  const esc = value => String(value ?? '').replace(/[&<>"']/g, character => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
  }[character]));

  const num = value => Number.isFinite(Number(value)) ? Number(value) : null;

  const dateText = value => {
    if (!value) return '—';
    const date = new Date(String(value).length === 10 ? `${value}T12:00:00Z` : value);
    return Number.isNaN(date.valueOf())
      ? String(value)
      : date.toLocaleDateString(locale, { year: 'numeric', month: 'short', day: '2-digit' });
  };

  const priceText = (value, currency) => {
    const amount = num(value);
    if (amount === null) return '—';
    const digits = currency === 'DKK' ? 4 : amount < 10 ? 4 : 2;
    return `${amount.toLocaleString(locale, {
      minimumFractionDigits: 2,
      maximumFractionDigits: digits
    })} ${currency || ''}`.trim();
  };

  function usdPlnRate(portfolio) {
    const direct = num(portfolio?.reporting_fx?.usd_pln);
    if (direct && direct > 0) return direct;

    for (const position of portfolio?.positions || []) {
      if (position?.currency !== 'USD') continue;
      const rate = num(position.current_fx_to_pln) || num(position.entry_fx_to_pln);
      if (rate && rate > 0) return rate;
    }
    return null;
  }

  function reportMoney(plnValue, portfolio, signed = false) {
    let amount = num(plnValue);
    if (amount === null) return '—';

    if (!isPl) {
      const rate = usdPlnRate(portfolio);
      if (!rate) return '— USD';
      amount /= rate;
    }

    const sign = signed && amount > 0 ? '+' : '';
    return `${sign}${amount.toLocaleString(locale, {
      minimumFractionDigits: 2,
      maximumFractionDigits: 2
    })} ${reportCurrency}`;
  }

  function percentText(value) {
    const amount = num(value);
    if (amount === null) return '—';
    const sign = amount > 0 ? '+' : '';
    return `${sign}${(amount * 100).toLocaleString(locale, {
      minimumFractionDigits: 2,
      maximumFractionDigits: 2
    })}%`;
  }

  function render(portfolio) {
    const closed = (portfolio?.closed_positions || []).filter(position =>
      position?.exit_timestamp_utc && num(position?.exit_value_pln) !== null
    );
    if (!closed.length) return false;

    const auditBody = document.getElementById('audit-body');
    const registerPanel = auditBody?.closest('section.panel');
    if (!registerPanel) return false;

    let section = document.getElementById('closed-transactions-history');
    if (!section) {
      section = document.createElement('section');
      section.id = 'closed-transactions-history';
      section.className = 'panel';
      registerPanel.insertAdjacentElement('afterend', section);
    }

    const rows = [...closed]
      .sort((a, b) => String(b.exit_timestamp_utc).localeCompare(String(a.exit_timestamp_utc)))
      .map(position => {
        const entryValue = num(position.entry_value_pln);
        const exitValue = num(position.exit_value_pln);
        const result = entryValue === null || exitValue === null ? null : exitValue - entryValue;
        const resultPercent = result === null || !entryValue ? null : result / entryValue;
        const tone = result > 0 ? 'positive' : result < 0 ? 'negative' : 'neutral';

        return `<tr>
          <td><b>${esc(position.broker_symbol || position.market_symbol || position.id || '—')}</b><br>${esc(position.label || '')}</td>
          <td>${esc(dateText(position.entry_date || position.entry_timestamp_utc))}</td>
          <td>${esc(priceText(position.entry_price, position.currency))}</td>
          <td>${esc(dateText(position.exit_timestamp_utc))}</td>
          <td>${esc(priceText(position.exit_price, position.currency))}</td>
          <td>${num(position.quantity) === null ? '—' : num(position.quantity).toLocaleString(locale, { maximumFractionDigits: 6 })}</td>
          <td>${esc(reportMoney(entryValue, portfolio))}</td>
          <td>${esc(reportMoney(exitValue, portfolio))}</td>
          <td class="${tone}"><b>${esc(reportMoney(result, portfolio, true))}</b><br><small>${esc(percentText(resultPercent))}</small></td>
          <td>${esc(T.sold)}</td>
        </tr>`;
      }).join('');

    section.innerHTML = `
      <div class="panel-head">
        <div>
          <h2>${esc(T.title)}</h2>
          <p>${esc(T.subtitle)}</p>
        </div>
      </div>
      <div class="table-scroll">
        <table class="audit">
          <thead><tr>
            <th>${esc(T.instrument)}</th>
            <th>${esc(T.purchaseDate)}</th>
            <th>${esc(T.purchasePrice)}</th>
            <th>${esc(T.saleDate)}</th>
            <th>${esc(T.salePrice)}</th>
            <th>${esc(T.units)}</th>
            <th>${esc(T.purchaseCapital)}</th>
            <th>${esc(T.saleValue)}</th>
            <th>${esc(T.result)}</th>
            <th>${esc(T.status)}</th>
          </tr></thead>
          <tbody>${rows}</tbody>
        </table>
      </div>`;

    document.body.dataset.closedTransactionsHistory = 'ready';
    return true;
  }

  async function start() {
    try {
      const response = await fetch(`/data/portfolio10k/paper_portfolio.json?v=${Date.now()}`, {
        cache: 'no-store'
      });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const portfolio = await response.json();

      let attempts = 0;
      const apply = () => {
        render(portfolio);
        attempts += 1;
        if (attempts < 24 && document.body.dataset.closedTransactionsHistory !== 'ready') {
          window.setTimeout(apply, 300);
        }
      };

      apply();
      window.addEventListener('hashchange', () => window.setTimeout(apply, 0));
      document.addEventListener('click', event => {
        if (event.target.closest('[data-tab]')) window.setTimeout(apply, 0);
      });
    } catch (_) {
      // The current History view remains unchanged if closed-position data is unavailable.
    }
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', start, { once: true });
  } else {
    start();
  }
})();
