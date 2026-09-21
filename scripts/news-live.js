(() => {
  'use strict';

  const lang = document.documentElement.lang === 'en' ? 'en' : 'pl';
  const feedUrl = `/data/news/${lang}.json`;
  const HOME_MAX_AGE_MS = 24 * 60 * 60 * 1000;
  const FUTURE_TOLERANCE_MS = 10 * 60 * 1000;
  const HOME_LIMIT = 12;
  const HOME_POLICY = 'max-24h-public-news-display-v1';
  const HOME_IMAGE_POLICY = 'https-image-required-v1';
  let expiryTimer = null;
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

  function isFreshNewsStory(story, now = Date.now()) {
    if (!story || !isFreshTimestamp(story.published_at, now)) return false;
    const firstSeen = story.news_first_seen_at || story.homepage_first_seen_at;
    if (!firstSeen) return true;
    return isFreshTimestamp(firstSeen, now);
  }

  function isFreshHomepageStory(story, now = Date.now()) {
    return isFreshNewsStory(story, now);
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
      });

    // The server-side homepage selector is the editorial authority. Preserve
    // its priority-lane order instead of re-sorting by recency in the browser.
    return fresh.slice(0, HOME_LIMIT);
  }

  function newsCard(story) {
    const link = safeHttp(story.link);
    const image = safeImage(story.image);
    const publishedAt = timestamp(story.published_at) ? String(story.published_at) : '';
    const firstSeenAt = timestamp(story.news_first_seen_at) ? String(story.news_first_seen_at) : '';
    const expiresAt = timestamp(story.news_expires_at) ? String(story.news_expires_at) : '';
    if (!link || !image || !story.title || !publishedAt) return '';
    const freshnessAttrs =
      ` data-news-published-at="${esc(publishedAt)}"` +
      (firstSeenAt ? ` data-news-first-seen-at="${esc(firstSeenAt)}"` : '') +
      (expiresAt ? ` data-news-expires-at="${esc(expiresAt)}"` : '');
    return `<li><a class="news-main-link" href="${esc(link)}"${freshnessAttrs} target="_blank" rel="noopener noreferrer external"><span class="news-thumb has-image"><img src="${esc(image)}" alt="" loading="lazy" decoding="async" referrerpolicy="no-referrer"></span><span class="news-title-wrap"><span class="news-text">${esc(story.title)}</span><span class="source-line">${text.source}: ${esc(story.source || '')}</span></span></a></li>`;
  }

  function homeCard(story) {
    const link = safeHttp(story.link);
    const image = safeImage(story.image);
    const publishedAt = timestamp(story.published_at) ? String(story.published_at) : '';
    const rawFirstSeen = story.news_first_seen_at || story.homepage_first_seen_at;
    const firstSeenAt = timestamp(rawFirstSeen) ? String(rawFirstSeen) : '';
    const rawExpires = story.news_expires_at || story.homepage_expires_at;
    const expiresAt = timestamp(rawExpires) ? String(rawExpires) : '';
    if (!link || !image || !story.title || !publishedAt) return '';
    const firstSeenAttr = firstSeenAt ? ` data-home-first-seen-at="${esc(firstSeenAt)}"` : '';
    const expiresAttr = expiresAt ? ` data-home-expires-at="${esc(expiresAt)}"` : '';
    return `<a class="brief-card" href="${esc(link)}" target="_blank" rel="noopener noreferrer external" data-home-published-at="${esc(publishedAt)}"${firstSeenAttr}${expiresAttr}><div class="thumb has-image"><img src="${esc(image)}" alt="" loading="lazy" decoding="async" referrerpolicy="no-referrer" data-br-external-media="source-linked" data-br-source-url="${esc(link)}"><span class="media-source-badge">${text.source}: ${esc(story.source || '')}</span></div><div class="brief-body"><h3 class="brief-title">${esc(story.title)}</h3><p class="brief-desc">${esc(story.summary || story.title)}</p><span class="brief-source"><b>${esc(story.source || text.source)}</b><span class="brief-link">${text.read}</span></span></div></a>`;
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
    const now = Date.now();
    let changed = false;
    Object.entries(data.sections || {}).forEach(([sectionId, stories]) => {
      const list = document.querySelector(`section#${CSS.escape(sectionId)} ul.news`);
      if (!list || !Array.isArray(stories)) return;
      const cards = stories
        .filter(story => isFreshNewsStory(story, now))
        .map(newsCard)
        .filter(Boolean)
        .join('');
      list.innerHTML = cards;
      changed = true;
    });
    return changed;
  }

  function pruneStaticNewsPage(now = Date.now()) {
    if (!document.body.matches('[data-page="news"]')) return;
    document.querySelectorAll('section.card ul.news li').forEach(item => {
      const link = item.querySelector('a.news-main-link');
      if (!link) {
        item.remove();
        return;
      }
      const sourceFresh = isFreshTimestamp(link.dataset.newsPublishedAt, now);
      const firstSeen = link.dataset.newsFirstSeenAt;
      const exposureFresh = !firstSeen || isFreshTimestamp(firstSeen, now);
      const expiresAt = timestamp(link.dataset.newsExpiresAt);
      const notExpired = !expiresAt || now <= expiresAt;
      const image = link.querySelector('.news-thumb.has-image img');
      const imageEligible = Boolean(image && safeImage(image.getAttribute('src')));
      if (sourceFresh && exposureFresh && notExpired && imageEligible) return;
      item.remove();
    });
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
    container.dataset.homePriority = 'politics-geopolitics-economy-ai-science-health-sport';
    if (!cards) {
      container.replaceChildren();
      return true;
    }
    container.innerHTML = cards;
    return true;
  }

  function cardExpiryTimestamp(node, publishedKey, firstSeenKey, expiresKey) {
    if (!node || !node.dataset) return 0;
    const explicit = timestamp(node.dataset[expiresKey]);
    if (explicit) return explicit;
    const firstSeen = timestamp(node.dataset[firstSeenKey]);
    if (firstSeen) return firstSeen + HOME_MAX_AGE_MS;
    const published = timestamp(node.dataset[publishedKey]);
    return published ? published + HOME_MAX_AGE_MS : 0;
  }

  function scheduleNextExpiry() {
    if (expiryTimer) {
      clearTimeout(expiryTimer);
      expiryTimer = null;
    }
    const now = Date.now();
    const expiries = [];
    document.querySelectorAll('a.news-main-link').forEach(link => {
      const expiry = cardExpiryTimestamp(
        link,
        'newsPublishedAt',
        'newsFirstSeenAt',
        'newsExpiresAt'
      );
      if (expiry > now) expiries.push(expiry);
    });
    document.querySelectorAll('#latest-briefs .brief-card').forEach(card => {
      const expiry = cardExpiryTimestamp(
        card,
        'homePublishedAt',
        'homeFirstSeenAt',
        'homeExpiresAt'
      );
      if (expiry > now) expiries.push(expiry);
    });
    if (!expiries.length) return;
    const nextExpiry = Math.min(...expiries);
    const delay = Math.max(0, Math.min(2147483647, nextExpiry - now + 25));
    expiryTimer = setTimeout(() => {
      pruneStaticNewsPage();
      pruneStaticHomepage();
      refresh();
    }, delay);
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
      scheduleNextExpiry();
      return true;
    } catch (error) {
      pruneStaticNewsPage();
      pruneStaticHomepage();
      removeLegacyHealthBanner();
      console.warn('BriefRooms live news refresh failed; public news keeps only <=24h stories with HTTPS images.', error);
      scheduleNextExpiry();
      return false;
    }
  }

  function start() {
    removeLegacyHealthBanner();
    pruneStaticNewsPage();
    pruneStaticHomepage();
    scheduleNextExpiry();
    refresh();
    setInterval(refresh, 15 * 60 * 1000);

    const observer = new MutationObserver(() => scheduleNextExpiry());
    const home = document.getElementById('latest-briefs');
    if (home) observer.observe(home, {childList: true});
    document.querySelectorAll('section.card ul.news').forEach(list => {
      observer.observe(list, {childList: true});
    });
    document.addEventListener('visibilitychange', () => {
      if (!document.hidden) refresh();
    });
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start, {once: true});
  else start();
})();
