(() => {
  'use strict';

  const lang = document.documentElement.lang === 'en' ? 'en' : 'pl';
  const feedUrl = `/data/news/${lang}.json`;
  const HOME_MAX_AGE_MS = 3 * 24 * 60 * 60 * 1000;
  const FUTURE_TOLERANCE_MS = 10 * 60 * 1000;
  const HOME_LIMIT = 10;
  const HOME_POLICY = 'max-72h-first-display-v1';
  const HOME_IMAGE_POLICY = 'https-image-required-v1';
  const TOPIC_ORDER = ['politics', 'economy', 'health'];
  const text = lang === 'pl' ? {
    source: 'Źródło',
    read: 'Czytaj źródło →',
    updated: 'Ostatnia aktualizacja',
  } : {
    source: 'Source',
    read: 'Read source →',
    updated: 'Last updated',
  };

  const esc = value => String(value ?? '').replace(/[&<>"']/g, char => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
  }[char]));

  function safeHttp(value) {
    try {
      const url = new URL(String(value || ''), location.href);
      if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password) return '';
      return url.href;
    } catch (_) {
      return '';
    }
  }

  function safeImage(value) {
    try {
      const url = new URL(String(value || ''), location.href);
      if (url.protocol !== 'https:' || url.username || url.password) return '';
      return url.href;
    } catch (_) {
      return '';
    }
  }

  function timestamp(value) {
    const parsed = Date.parse(String(value || ''));
    return Number.isFinite(parsed) ? parsed : 0;
  }

  function isFreshTimestamp(value, now = Date.now()) {
    const published = typeof value === 'number' ? value : timestamp(value);
    if (!published) return false;
    const age = now - published;
    return age >= -FUTURE_TOLERANCE_MS && age <= HOME_MAX_AGE_MS;
  }

  function isFreshHomepageStory(story, now = Date.now()) {
    if (!story || !isFreshTimestamp(story.published_at, now)) return false;
    if (!story.homepage_first_seen_at) return true;
    return isFreshTimestamp(story.homepage_first_seen_at, now);
  }

  function normalizedCategory(value) {
    return String(value || '')
      .normalize('NFD')
      .replace(/[\u0300-\u036f]/g, '')
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, ' ')
      .trim();
  }

  function topicForCategory(value) {
    const category = normalizedCategory(value);
    if (!category) return '';
    if (
      /\b(polityka|polityczny|geopolityka|politics|political|geopolitics|geopolitical)\b/.test(category) ||
      /\b(world news|europe|middle east|asia pacific)\b/.test(category)
    ) return 'politics';
    if (/\b(ekonomia|gospodarka|biznes|rynki|finanse|economy|economic|business|markets|finance|financial)\b/.test(category)) {
      return 'economy';
    }
    if (/\b(zdrowie|medycyna|health|medicine|medical)\b/.test(category)) return 'health';
    return '';
  }

  function storyIdentity(story) {
    return String(story && (story.link || story.title) || '').trim();
  }

  function selectHomepageStories(stories, now = Date.now()) {
    const seen = new Set();
    const fresh = (Array.isArray(stories) ? stories : [])
      .filter(story => {
        if (!story || !story.title || !safeImage(story.image) || !isFreshHomepageStory(story, now)) return false;
        const identity = storyIdentity(story);
        if (!identity || seen.has(identity)) return false;
        seen.add(identity);
        return true;
      })
      .sort((a, b) => timestamp(b.published_at) - timestamp(a.published_at));

    const selected = [];
    const selectedIds = new Set();
    const add = story => {
      if (!story) return;
      const identity = storyIdentity(story);
      if (!identity || selectedIds.has(identity)) return;
      selected.push(story);
      selectedIds.add(identity);
    };

    TOPIC_ORDER.forEach(topic => add(fresh.find(story => topicForCategory(story.category) === topic)));
    fresh.filter(story => topicForCategory(story.category)).forEach(add);
    fresh.forEach(add);
    return selected.slice(0, HOME_LIMIT);
  }

  function newsCard(story) {
    const link = safeHttp(story.link);
    const image = safeHttp(story.image);
    if (!link || !image || !story.title) return '';
    return `<li><a class="news-main-link" href="${esc(link)}" target="_blank" rel="noopener noreferrer external"><span class="news-thumb has-image"><img src="${esc(image)}" alt="" loading="lazy" decoding="async" referrerpolicy="no-referrer"></span><span class="news-title-wrap"><span class="news-text">${esc(story.title)}</span><span class="source-line">${text.source}: ${esc(story.source || '')}</span></span></a></li>`;
  }

  function homeCard(story) {
    const link = safeHttp(story.link);
    const image = safeImage(story.image);
    const publishedAt = timestamp(story.published_at) ? String(story.published_at) : '';
    const firstSeenAt = timestamp(story.homepage_first_seen_at) ? String(story.homepage_first_seen_at) : '';
    if (!link || !image || !story.title || !publishedAt) return '';
    const firstSeenAttr = firstSeenAt ? ` data-home-first-seen-at="${esc(firstSeenAt)}"` : '';
    return `<a class="brief-card" href="${esc(link)}" target="_blank" rel="noopener noreferrer external" data-home-published-at="${esc(publishedAt)}"${firstSeenAttr}><div class="thumb has-image"><img src="${esc(image)}" alt="" loading="lazy" decoding="async" referrerpolicy="no-referrer" data-br-external-media="source-linked" data-br-source-url="${esc(link)}"><span class="media-source-badge">${text.source}: ${esc(story.source || '')}</span></div><div class="brief-body"><h3 class="brief-title">${esc(story.title)}</h3><p class="brief-desc">${esc(story.summary || story.title)}</p><span class="brief-source"><b>${esc(story.source || text.source)}</b><span class="brief-link">${text.read}</span></span></div></a>`;
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
    if (container) {
      container.dataset.homeUpdatedAt = data.generated_at;
      container.dataset.homeImagePolicy = HOME_IMAGE_POLICY;
    }
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

  function pruneStaticHomepage(now = Date.now()) {
    const container = document.getElementById('latest-briefs');
    if (!container) return;
    container.dataset.homeFreshnessPolicy = HOME_POLICY;
    container.dataset.homeImagePolicy = HOME_IMAGE_POLICY;
    container.querySelectorAll('.brief-card').forEach(card => {
      const sourceFresh = isFreshTimestamp(card.dataset.homePublishedAt, now);
      const exposureFresh = !card.dataset.homeFirstSeenAt || isFreshTimestamp(card.dataset.homeFirstSeenAt, now);
      const image = card.querySelector('.thumb.has-image img');
      const imageEligible = Boolean(image && safeImage(image.getAttribute('src')));
      if (sourceFresh && exposureFresh && imageEligible) {
        card.hidden = false;
        card.removeAttribute('aria-hidden');
        delete card.dataset.homeStale;
        return;
      }
      card.remove();
    });
  }

  function renderHomepage(data) {
    const container = document.getElementById('latest-briefs');
    if (!container || !Array.isArray(data.home)) return false;
    const stories = selectHomepageStories(data.home);
    const cards = stories.map(homeCard).filter(Boolean).join('');
    container.dataset.homeFreshnessPolicy = HOME_POLICY;
    container.dataset.homeImagePolicy = HOME_IMAGE_POLICY;
    container.dataset.homePriority = 'politics-economy-health';
    if (!cards) {
      container.replaceChildren();
      return true;
    }
    container.innerHTML = cards;
    return true;
  }

  function removeLegacyHealthBanner() {
    document.getElementById('news-live-health')?.remove();
  }

  async function refresh() {
    try {
      const response = await fetch(`${feedUrl}?v=${Date.now()}`, {
        cache: 'no-store',
        headers: {'Cache-Control': 'no-cache'},
      });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const data = await response.json();
      if (!data || !/^news-live-v[12]$/.test(String(data.schema_version || '')) || data.language !== lang) {
        throw new Error('invalid news feed');
      }
      const rendered = renderNewsPage(data) || renderHomepage(data);
      if (!rendered) return false;
      updateTimestamp(data);
      removeLegacyHealthBanner();
      document.documentElement.dataset.newsLiveMarker = String(data.marker || '');
      return true;
    } catch (error) {
      pruneStaticHomepage();
      removeLegacyHealthBanner();
      console.warn('BriefRooms live news refresh failed; homepage keeps only fresh stories with HTTPS images.', error);
      return false;
    }
  }

  function start() {
    removeLegacyHealthBanner();
    pruneStaticHomepage();
    refresh();
    setInterval(refresh, 15 * 60 * 1000);
    document.addEventListener('visibilitychange', () => {
      if (!document.hidden) refresh();
    });
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start, {once: true});
  else start();
})();
