(() => {
  'use strict';
  // Separate breaking rail: never mutates the canonical homepage or section cards.
  const lang = document.documentElement.lang === 'en' ? 'en' : 'pl';
  const FEED = '/data/news/urgent.json';
  const ID = 'briefrooms-urgent';
  const SOURCE_AGE_MS = 75 * 60 * 1000;
  const DISPLAY_MS = 90 * 60 * 1000;
  const HEARTBEAT_MS = 45 * 60 * 1000;
  const POLL_MS = 3 * 60 * 1000;
  const label = lang === 'pl' ? 'PILNE · zweryfikowane wydarzenia' : 'BREAKING · verified events';
  const sourceLabel = lang === 'pl' ? 'Źródło' : 'Source';
  const escapeHtml = value => String(value ?? '').replace(/[&<>"']/g, char => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
  }[char]));

  function parseTime(value) {
    const time = Date.parse(String(value || ''));
    return Number.isFinite(time) ? time : 0;
  }
  function safeLink(value) {
    try {
      const parsed = new URL(String(value || ''), location.href);
      return parsed.protocol === 'https:' && !parsed.username && !parsed.password ? parsed.href : '';
    } catch (_) {
      return '';
    }
  }
  function identity(value) {
    try {
      const parsed = new URL(String(value || ''), location.href);
      return parsed.hostname.toLowerCase() + parsed.pathname.replace(/\/+$/, '').toLowerCase();
    } catch (_) {
      return '';
    }
  }
  function qualified(story, now) {
    if (!story || !story.title || !story.source || !story.canonical_event_id) return false;
    if (!safeLink(story.link) || !safeLink(story.image)) return false;
    if (story.claim_consistency_status === 'disputed') return false;
    const published = parseTime(story.published_at);
    const first = parseTime(story.news_first_seen_at);
    const expires = parseTime(story.news_expires_at);
    return Boolean(published && first && expires &&
      published <= now + 2 * 60 * 1000 && now - published <= SOURCE_AGE_MS &&
      first <= now + 2 * 60 * 1000 && expires > now &&
      expires <= Math.min(first, published) + DISPLAY_MS);
  }
  function currentPageIdentities() {
    const seen = new Set();
    document.querySelectorAll(
      'section.card:not(#briefrooms-urgent) a.news-main-link, #latest-briefs a.brief-card'
    ).forEach(link => {
      const item = identity(link.getAttribute('href'));
      if (item) seen.add(item);
    });
    return seen;
  }
  function position() {
    if (document.body.matches('[data-page="news"]')) {
      return document.querySelector('main section.card');
    }
    const container = document.getElementById('latest-briefs');
    return container ? container.closest('section') : null;
  }
  function remove() {
    document.getElementById(ID)?.remove();
  }
  function render(data) {
    const now = Date.now();
    const checked = parseTime(data && data.checked_at);
    if (!data || data.schema_version !== 'briefrooms-urgent-news-v1' ||
        !checked || checked > now + 2 * 60 * 1000 || now - checked > HEARTBEAT_MS) {
      remove();
      return;
    }
    const lane = data.languages && data.languages[lang];
    if (!lane || !Array.isArray(lane.stories)) {
      remove();
      return;
    }
    const existing = currentPageIdentities();
    const dedupe = new Set();
    const stories = lane.stories.filter(story => {
      if (!qualified(story, now)) return false;
      const id = identity(story.link);
      if (!id || existing.has(id) || dedupe.has(id)) return false;
      dedupe.add(id);
      return true;
    }).slice(0, 3);
    const reference = position();
    if (!stories.length || !reference) {
      remove();
      return;
    }
    let box = document.getElementById(ID);
    if (!box) {
      box = document.createElement('section');
      box.id = ID;
      box.className = document.body.matches('[data-page="news"]') ? 'card' : 'source-card';
      box.setAttribute('aria-label', label);
      reference.parentNode.insertBefore(box, reference);
    }
    const rows = stories.map(story => {
      const published = escapeHtml(story.published_at);
      const first = escapeHtml(story.news_first_seen_at);
      const expires = escapeHtml(story.news_expires_at);
      return '<li><a class="news-main-link" href="' + escapeHtml(safeLink(story.link)) +
        '" data-news-published-at="' + published + '" data-news-first-seen-at="' + first +
        '" data-news-expires-at="' + expires +
        '" target="_blank" rel="noopener noreferrer external">' +
        '<span class="news-thumb has-image"><img src="' + escapeHtml(safeLink(story.image)) +
        '" alt="" loading="lazy" decoding="async" referrerpolicy="no-referrer"></span>' +
        '<span class="news-title-wrap"><span class="news-text">' + escapeHtml(story.title) +
        '</span><span class="source-line">' + sourceLabel + ': ' + escapeHtml(story.source) +
        '</span></span></a></li>';
    }).join('');
    box.innerHTML = '<h2>' + label + '</h2><ul class="news">' + rows + '</ul>';
  }
  async function refresh() {
    try {
      const response = await fetch(FEED + '?u=' + Date.now(), {
        cache: 'no-store',
        headers: { 'Cache-Control': 'no-cache' }
      });
      if (!response.ok) throw new Error('Urgent HTTP ' + response.status);
      render(await response.json());
    } catch (_) {
      // Fail closed in this rail without affecting regular news.
      remove();
    }
  }
  function start() {
    if (!document.getElementById('briefrooms-urgent-css')) {
      const stylesheet = document.createElement('link');
      stylesheet.id = 'briefrooms-urgent-css';
      stylesheet.rel = 'stylesheet';
      stylesheet.href = '/scripts/urgent-news.css?v=1';
      document.head.appendChild(stylesheet);
    }
    refresh();
    setInterval(refresh, POLL_MS);
    document.addEventListener('visibilitychange', () => {
      if (!document.hidden) refresh();
    });
  }
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', start, { once: true });
  } else {
    start();
  }
})();
