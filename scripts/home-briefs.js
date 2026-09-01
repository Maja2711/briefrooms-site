(function (root, factory) {
  'use strict';

  var api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;

  if (root.document) {
    var start = function () {
      api.loadHome({
        document: root.document,
        fetchImpl: root.fetch ? root.fetch.bind(root) : null,
        lang: root.document.documentElement.lang === 'en' ? 'en' : 'pl',
        console: root.console
      });
    };
    if (root.document.readyState === 'loading') {
      root.document.addEventListener('DOMContentLoaded', start, { once: true });
    } else {
      start();
    }
  }
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';

  var QUALITY_STATUS = 'passed_strict_v7';
  var CARD_LIMIT = 10;
  var HOME_MAX_AGE_MS = 3 * 24 * 60 * 60 * 1000;
  var FUTURE_TOLERANCE_MS = 10 * 60 * 1000;
  var TOPIC_ORDER = ['politics', 'economy', 'health'];
  var CONFIG = {
    pl: {
      feed: '/pl/home_brief.json',
      fallback: '/pl/aktualnosci.html',
      source: 'Źródło',
      read: 'Czytaj brief →',
      updated: 'Aktualizacja: ',
      locale: 'pl-PL',
      briefPath: /^\/pl\/briefy\/[a-z0-9-]+-[0-9a-f]{12}\.html$/
    },
    en: {
      feed: '/en/home_brief.json',
      fallback: '/en/news.html',
      source: 'Source',
      read: 'Read brief →',
      updated: 'Update: ',
      locale: 'en-GB',
      briefPath: /^\/en\/briefs\/[a-z0-9-]+-[0-9a-f]{12}\.html$/
    }
  };

  function configFor(lang) {
    return lang === 'en' ? CONFIG.en : CONFIG.pl;
  }

  function safeHttpUrl(value) {
    var raw = String(value || '').trim();
    if (!raw) return '';
    try {
      var parsed = new URL(raw, 'https://briefrooms.com');
      if (parsed.protocol !== 'http:' && parsed.protocol !== 'https:') return '';
      if (parsed.username || parsed.password) return '';
      return parsed.href;
    } catch (error) {
      return '';
    }
  }

  function safePermalink(value, lang) {
    var path = String(value || '').trim();
    return configFor(lang).briefPath.test(path) ? path : '';
  }

  function timestamp(value) {
    var parsed = Date.parse(String(value || ''));
    return Number.isFinite(parsed) ? parsed : 0;
  }

  function isFresh(item, nowMs) {
    var published = timestamp(item && item.published_at);
    if (!published) return false;
    var now = Number.isFinite(nowMs) ? nowMs : Date.now();
    var age = now - published;
    return age >= -FUTURE_TOLERANCE_MS && age <= HOME_MAX_AGE_MS;
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
    var category = normalizedCategory(value);
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

  function isApproved(item) {
    return Boolean(
      item &&
      item.comment_quality_status === QUALITY_STATUS &&
      item.comment_quality_version === 7 &&
      item.summary_basis === 'article_text_ai_reviewed' &&
      item.comment_generation_status === 'ai_review_approved' &&
      typeof item.full_brief === 'string' &&
      item.full_brief.trim()
    );
  }

  function selectApproved(items, lang, nowMs) {
    var seen = new Set();
    var fresh = (Array.isArray(items) ? items : []).filter(function (item) {
      if (!isApproved(item) || !safePermalink(item.permalink, lang) || !isFresh(item, nowMs)) return false;
      var identity = String(item.link || item.title || '');
      if (!identity || seen.has(identity)) return false;
      seen.add(identity);
      return true;
    }).sort(function (a, b) {
      return timestamp(b.published_at) - timestamp(a.published_at);
    });

    var selected = [];
    var selectedIds = new Set();
    function add(item) {
      if (!item) return;
      var identity = String(item.link || item.title || '');
      if (!identity || selectedIds.has(identity)) return;
      selectedIds.add(identity);
      selected.push(item);
    }

    TOPIC_ORDER.forEach(function (topic) {
      add(fresh.find(function (item) { return topicForCategory(item.category) === topic; }));
    });
    fresh.filter(function (item) { return Boolean(topicForCategory(item.category)); }).forEach(add);
    fresh.forEach(add);
    return selected.slice(0, CARD_LIMIT);
  }

  function fallbackLabel(category) {
    return Array.from(String(category || 'BR').trim()).slice(0, 2).join('').toUpperCase() || 'BR';
  }

  function element(document, tag, className, text) {
    var node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = String(text);
    return node;
  }

  function createCard(document, item, lang) {
    var cfg = configFor(lang);
    var card = element(document, 'a', 'brief-card');
    card.href = safePermalink(item.permalink, lang) || cfg.fallback;
    if (timestamp(item.published_at)) card.dataset.homePublishedAt = String(item.published_at);

    var imageUrl = safeHttpUrl(item.image);
    var thumb = element(document, 'div', imageUrl ? 'thumb has-image' : 'thumb');
    var fallback = element(document, 'div', 'fallback-art', fallbackLabel(item.category));
    fallback.setAttribute('aria-hidden', 'true');
    thumb.appendChild(fallback);
    if (imageUrl) {
      var image = element(document, 'img');
      image.src = imageUrl;
      image.alt = '';
      image.loading = 'lazy';
      image.referrerPolicy = 'no-referrer';
      image.addEventListener('error', function () {
        image.remove();
        thumb.classList.remove('has-image');
      }, { once: true });
      thumb.appendChild(image);
    }

    var body = element(document, 'div', 'brief-body');
    body.appendChild(element(document, 'h3', 'brief-title', item.title));
    body.appendChild(element(document, 'p', 'brief-desc', item.full_brief));
    var sourceLine = element(document, 'span', 'brief-source');
    sourceLine.appendChild(element(document, 'b', '', item.source || cfg.source));
    sourceLine.appendChild(element(document, 'span', 'brief-link', cfg.read));
    body.appendChild(sourceLine);
    card.appendChild(thumb);
    card.appendChild(body);
    return card;
  }

  function renderBriefs(document, items, lang, nowMs) {
    var container = document.getElementById('latest-briefs');
    var approved = selectApproved(items, lang, nowMs);
    if (!container || !approved.length) return false;
    var fragment = document.createDocumentFragment();
    approved.forEach(function (item) {
      fragment.appendChild(createCard(document, item, lang));
    });
    container.replaceChildren(fragment);
    container.dataset.homeFreshnessPolicy = 'max-72h-v1';
    container.dataset.homePriority = 'politics-economy-health';
    return true;
  }

  function updateLabel(node, updatedAt, lang) {
    var cfg = configFor(lang);
    var date = new Date(updatedAt);
    node.textContent = cfg.updated + new Intl.DateTimeFormat(cfg.locale, {
      day: '2-digit',
      month: '2-digit',
      year: 'numeric',
      timeZone: 'UTC'
    }).format(date);
  }

  async function loadHome(options) {
    var document = options.document;
    var fetchImpl = options.fetchImpl;
    var lang = options.lang === 'en' ? 'en' : 'pl';
    var logger = options.console || { warn: function () {} };
    var renderer = options.renderer || renderBriefs;
    var container = document && document.getElementById('latest-briefs');
    var label = document && document.getElementById('updated-at');
    if (!container || !label || typeof fetchImpl !== 'function') return false;

    var staticTimestamp = timestamp(container.dataset.homeUpdatedAt);
    try {
      var response = await fetchImpl(configFor(lang).feed + '?v=' + Date.now(), { cache: 'no-store' });
      if (!response || !response.ok) throw new Error('homepage feed request failed');
      var data = await response.json();
      var feedTimestamp = timestamp(data && data.updated_at);
      var nowMs = Date.now();
      var items = selectApproved([
        ...(Array.isArray(data && data.latest) ? data.latest : []),
        ...(Array.isArray(data && data.radar) ? data.radar : [])
      ], lang, nowMs);
      if (!items.length || !feedTimestamp || feedTimestamp <= staticTimestamp) return false;
      if (!renderer(document, items, lang, nowMs)) return false;
      container.dataset.homeUpdatedAt = String(data.updated_at);
      updateLabel(label, data.updated_at, lang);
      return true;
    } catch (error) {
      logger.warn('BriefRooms homepage feed could not be refreshed; only static cards within the 72-hour policy may remain visible.', error);
      return false;
    }
  }

  return {
    CARD_LIMIT: CARD_LIMIT,
    HOME_MAX_AGE_MS: HOME_MAX_AGE_MS,
    QUALITY_STATUS: QUALITY_STATUS,
    createCard: createCard,
    isApproved: isApproved,
    isFresh: isFresh,
    loadHome: loadHome,
    renderBriefs: renderBriefs,
    safeHttpUrl: safeHttpUrl,
    safePermalink: safePermalink,
    selectApproved: selectApproved,
    timestamp: timestamp,
    topicForCategory: topicForCategory
  };
});