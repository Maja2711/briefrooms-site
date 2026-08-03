(() => {
  'use strict';

  const lang = document.documentElement.lang === 'en' ? 'en' : 'pl';
  const feedUrl = `/data/news/${lang}.json`;
  const text = lang === 'pl' ? {
    source: 'Źródło',
    read: 'Czytaj źródło →',
    updated: 'Ostatnia aktualizacja',
    degraded: 'Część źródeł jest chwilowo niedostępna. Pokazujemy najnowsze potwierdzone materiały.',
  } : {
    source: 'Source',
    read: 'Read source →',
    updated: 'Last updated',
    degraded: 'Some sources are temporarily unavailable. The latest confirmed items remain visible.',
  };

  const esc = value => String(value ?? '').replace(/[&<>"']/g, char => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
  }[char]));

  function safeHttp(value) {
    try {
      const url = new URL(String(value || ''), location.href);
      return ['http:', 'https:'].includes(url.protocol) ? url.href : '';
    } catch (_) {
      return '';
    }
  }

  function newsCard(story) {
    const link = safeHttp(story.link);
    const image = safeHttp(story.image);
    if (!link || !image || !story.title) return '';
    return `<li><a class="news-main-link" href="${esc(link)}" target="_blank" rel="noopener noreferrer external"><span class="news-thumb has-image"><img src="${esc(image)}" alt="" loading="lazy" decoding="async" referrerpolicy="no-referrer"></span><span class="news-title-wrap"><span class="news-text">${esc(story.title)}</span><span class="source-line">${text.source}: ${esc(story.source || '')}</span></span></a></li>`;
  }

  function homeCard(story) {
    const link = safeHttp(story.link);
    const image = safeHttp(story.image);
    if (!link || !image || !story.title) return '';
    return `<a class="brief-card" href="${esc(link)}" target="_blank" rel="noopener noreferrer external"><div class="thumb has-image"><div class="fallback-art" aria-hidden="true">BR</div><img src="${esc(image)}" alt="" loading="lazy" decoding="async" referrerpolicy="no-referrer" data-br-external-media="source-linked" data-br-source-url="${esc(link)}"><span class="media-source-badge">${text.source}: ${esc(story.source || '')}</span></div><div class="brief-body"><h3 class="brief-title">${esc(story.title)}</h3><p class="brief-desc">${esc(story.summary || story.title)}</p><span class="brief-source"><b>${esc(story.source || text.source)}</b><span class="brief-link">${text.read}</span></span></div></a>`;
  }

  function formatTime(value) {
    const date = new Date(value);
    if (Number.isNaN(date.valueOf())) return '';
    return new Intl.DateTimeFormat(lang === 'pl' ? 'pl-PL' : 'en-GB', {
      day: '2-digit', month: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit',
      timeZone: 'Europe/Warsaw',
    }).format(date);
  }

  function updateTimestamp(data) {
    const formatted = formatTime(data.generated_at);
    if (!formatted) return;
    const timeNode = document.querySelector('time');
    if (timeNode) {
      timeNode.dateTime = data.generated_at;
      timeNode.textContent = `${text.updated}: ${formatted}`;
    }
    const label = document.getElementById('updated-at');
    if (label) label.textContent = `${text.updated}: ${formatted}`;
    const container = document.getElementById('latest-briefs');
    if (container) container.dataset.homeUpdatedAt = data.generated_at;
    let meta = document.querySelector('meta[name="briefrooms-news-updated-at"]');
    if (!meta) {
      meta = document.createElement('meta');
      meta.name = 'briefrooms-news-updated-at';
      document.head.appendChild(meta);
    }
    meta.content = data.generated_at;
  }

  function renderNewsPage(data) {
    if (!document.body.matches('[data-page="news"]')) return false;
    let changed = false;
    Object.entries(data.sections || {}).forEach(([sectionId, stories]) => {
      const list = document.querySelector(`section#${CSS.escape(sectionId)} ul.news`);
      if (!list || !Array.isArray(stories)) return;
      const cards = stories.map(newsCard).filter(Boolean).join('');
      if (!cards) return;
      list.innerHTML = cards;
      changed = true;
    });
    return changed;
  }

  function renderHomepage(data) {
    const container = document.getElementById('latest-briefs');
    if (!container || !Array.isArray(data.home)) return false;
    const cards = data.home.map(homeCard).filter(Boolean).slice(0, 10).join('');
    if (!cards) return false;
    container.innerHTML = cards;
    return true;
  }

  function renderHealth(data) {
    const status = data?.health?.status;
    const id = 'news-live-health';
    let node = document.getElementById(id);
    if (status !== 'degraded') {
      if (node) node.remove();
      return;
    }
    if (!node) {
      node = document.createElement('p');
      node.id = id;
      node.setAttribute('role', 'status');
      node.style.cssText = 'max-width:1180px;margin:10px auto;padding:10px 14px;border:1px solid rgba(255,191,63,.35);border-radius:12px;background:rgba(255,191,63,.09);color:inherit;font-size:13px;';
      const main = document.querySelector('main');
      if (main) main.prepend(node);
    }
    node.textContent = text.degraded;
  }

  async function refresh() {
    try {
      const response = await fetch(`${feedUrl}?v=${Date.now()}`, {
        cache: 'no-store',
        headers: {'Cache-Control': 'no-cache'},
      });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const data = await response.json();
      if (!data || data.schema_version !== 'news-live-v1' || data.language !== lang) throw new Error('invalid news feed');
      const rendered = renderNewsPage(data) || renderHomepage(data);
      if (!rendered) return false;
      updateTimestamp(data);
      renderHealth(data);
      document.documentElement.dataset.newsLiveMarker = String(data.marker || '');
      return true;
    } catch (error) {
      console.warn('BriefRooms live news refresh failed; static content remains visible.', error);
      return false;
    }
  }

  function start() {
    refresh();
    setInterval(refresh, 15 * 60 * 1000);
    document.addEventListener('visibilitychange', () => {
      if (!document.hidden) refresh();
    });
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start, {once: true});
  else start();
})();
