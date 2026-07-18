(() => {
  'use strict';

  const FALLBACK_DATES = {
    '/pl/geo/rosja-drony-paliwa-zboze.html': ['2026-07-14', '14.07.2026'],
    '/pl/geo/polska-panstwo-frontowe.html': ['2026-06-13', '13.06.2026'],
    '/pl/geo/ziemie-rzadkie.html': ['2025', '2025'],
    '/pl/geo/usa-chiny-2025.html': ['2025-10-25', '25.10.2025'],
    '/pl/geo/upadek-hegemona.html': ['2025-11-24', '24.11.2025'],
    '/en/geo/russia-drones-fuel-grain.html': ['2026-07-14', '14 Jul 2026'],
    '/en/geo/black-sea.html': ['2025', '2025'],
    '/en/geo/rare-earths.html': ['2025', '2025'],
    '/en/geo/falling-hegemon.html': ['2025-11-24', '24 Nov 2025'],
    '/en/geo/usa-china-2025.html': ['2025-10-25', '25 Oct 2025']
  };

  const lang = document.documentElement.lang?.toLowerCase().startsWith('en') ? 'en' : 'pl';
  const labels = lang === 'en'
    ? { publication: 'Publication date:', share: 'Share on X' }
    : { publication: 'Data publikacji:', share: 'Udostępnij na X' };

  function xIcon() {
    return '<svg viewBox="0 0 24 24" aria-hidden="true"><path fill="currentColor" d="M20.3 3 13.9 10.7 21 21h-4.8l-4.9-6.7L6 21H3.7l6.9-8.3L4 3h4.9l4.5 6.5L18.3 3h2z"/></svg>';
  }

  function canonicalUrl() {
    const canonical = document.querySelector('link[rel="canonical"]')?.href;
    return canonical || `${location.origin}${location.pathname}`;
  }

  function cleanTitle() {
    const heading = document.querySelector('header h1, main h1, h1')?.textContent?.trim();
    return heading || document.title.replace(/\s*[|—-]\s*BriefRooms\s*$/i, '').trim();
  }

  function dateFromMetadata() {
    const raw = document.querySelector('meta[property="article:published_time"]')?.content
      || document.querySelector('script[type="application/ld+json"]')?.textContent;

    if (raw && raw.trim().startsWith('{')) {
      try {
        const parsed = JSON.parse(raw);
        const value = parsed.datePublished;
        if (typeof value === 'string' && value) return value.slice(0, 10);
      } catch (_) {
        // A malformed legacy JSON-LD block must not block page rendering.
      }
    }

    if (raw && !raw.trim().startsWith('{')) return raw.slice(0, 10);
    return null;
  }

  function formatDate(value) {
    if (!value) return '';
    if (/^\d{4}$/.test(value)) return value;
    const parsed = new Date(`${value.slice(0, 10)}T12:00:00Z`);
    if (Number.isNaN(parsed.valueOf())) return value;
    return new Intl.DateTimeFormat(lang === 'en' ? 'en-GB' : 'pl-PL', {
      day: '2-digit', month: lang === 'en' ? 'short' : '2-digit', year: 'numeric'
    }).format(parsed);
  }

  function ensurePublicationDate(header) {
    let time = header.querySelector('time[datetime]');
    let row = time?.closest('p');

    if (time) {
      row?.classList.add('geo-publication-date');
      return row || time;
    }

    const fallback = FALLBACK_DATES[location.pathname];
    const iso = dateFromMetadata() || fallback?.[0];
    if (!iso) return null;

    row = document.createElement('p');
    row.className = 'geo-publication-date';
    const display = fallback?.[0] === iso ? fallback[1] : formatDate(iso);
    row.innerHTML = `📅 ${labels.publication} <time datetime="${iso}">${display}</time>`;

    const subtitle = header.querySelector('.sub, .lead, .tagline');
    if (subtitle) subtitle.insertAdjacentElement('afterend', row);
    else header.appendChild(row);
    return row;
  }

  function ensureShareButton(header, dateRow) {
    let button = header.querySelector('.geo-x-share, .tweet-share, #tweet-this');
    let row = button?.closest('p');

    if (!row) {
      row = document.createElement('p');
      if (button) row.appendChild(button);
    }

    row.classList.add('geo-share-row');

    if (!button) {
      button = document.createElement('a');
      row.appendChild(button);
    }

    button.id = 'tweet-this';
    button.className = 'geo-x-share tweet-share';
    button.target = '_blank';
    button.rel = 'noopener noreferrer';
    button.setAttribute('aria-label', labels.share);

    const text = `${cleanTitle()} — BriefRooms`;
    const shareUrl = new URL('https://twitter.com/intent/tweet');
    shareUrl.searchParams.set('url', canonicalUrl());
    shareUrl.searchParams.set('text', text);
    button.href = shareUrl.toString();
    button.innerHTML = `${xIcon()}<span>${labels.share}</span>`;

    if (dateRow) dateRow.insertAdjacentElement('afterend', row);
    else header.appendChild(row);
  }

  function init() {
    document.body.classList.add('geo-book-unified');
    const header = document.querySelector('body > header, header');
    if (!header) return;
    const dateRow = ensurePublicationDate(header);
    ensureShareButton(header, dateRow);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init, { once: true });
  } else {
    init();
  }
})();
