(function (root, factory) {
  'use strict';

  var api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;

  if (root.document) {
    var start = function () {
      api.start({
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

  var MIN_CARDS = 10;
  var HOME_MAX_AGE_MS = 3 * 24 * 60 * 60 * 1000;
  var FUTURE_TOLERANCE_MS = 10 * 60 * 1000;
  var RECHECK_DELAYS_MS = [650, 1500, 3500];

  function timestamp(value) {
    var parsed = Date.parse(String(value || ''));
    return Number.isFinite(parsed) ? parsed : 0;
  }

  function isFresh(value, nowMs) {
    var published = timestamp(value);
    if (!published) return false;
    var now = Number.isFinite(nowMs) ? nowMs : Date.now();
    var age = now - published;
    return age >= -FUTURE_TOLERANCE_MS && age <= HOME_MAX_AGE_MS;
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

  function storyIdentity(story) {
    return safeHttpUrl(story && story.link) || String(story && story.title || '').trim();
  }

  function cardIdentity(card) {
    if (!card) return '';
    var href = safeHttpUrl(card.getAttribute && card.getAttribute('href'));
    if (href) return href;
    var title = card.querySelector && card.querySelector('.brief-title');
    return String(title && title.textContent || '').trim();
  }

  function visibleCards(container) {
    return Array.from(container.querySelectorAll(':scope > .brief-card')).filter(function (card) {
      return !card.hidden && card.getAttribute('aria-hidden') !== 'true' && card.dataset.homeStale !== 'true';
    });
  }

  function fallbackLabel(category) {
    var value = String(category || 'BR').trim();
    return Array.from(value).slice(0, 2).join('').toUpperCase() || 'BR';
  }

  function element(document, tag, className, text) {
    var node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = String(text);
    return node;
  }

  function ensureFallback(card) {
    if (!card || !card.querySelector) return card;
    var thumb = card.querySelector('.thumb');
    if (!thumb) return card;
    var image = thumb.querySelector('img');
    if (image) image.remove();
    thumb.classList.remove('has-image');
    var fallback = thumb.querySelector('.fallback-art');
    if (!fallback) {
      fallback = element(card.ownerDocument, 'div', 'fallback-art', 'BR');
      fallback.setAttribute('aria-hidden', 'true');
      thumb.insertBefore(fallback, thumb.firstChild);
    }
    fallback.hidden = false;
    card.dataset.brPhotoGuarded = '1';
    card.dataset.homeCardFloor = 'image-fallback';
    return card;
  }

  function makeFallbackCard(document, story, lang, nowMs) {
    var link = safeHttpUrl(story && story.link);
    var published = String(story && story.published_at || '');
    if (!link || !story || !story.title || !isFresh(published, nowMs)) return null;

    var card = element(document, 'a', 'brief-card');
    card.href = link;
    card.target = '_blank';
    card.rel = 'noopener noreferrer external';
    card.dataset.homePublishedAt = published;
    if (story.homepage_first_seen_at) card.dataset.homeFirstSeenAt = String(story.homepage_first_seen_at);
    card.dataset.brPhotoGuarded = '1';
    card.dataset.homeCardFloor = 'feed-fallback';

    var thumb = element(document, 'div', 'thumb');
    var fallback = element(document, 'div', 'fallback-art', fallbackLabel(story.category));
    fallback.setAttribute('aria-hidden', 'true');
    thumb.appendChild(fallback);

    var body = element(document, 'div', 'brief-body');
    body.appendChild(element(document, 'h3', 'brief-title', story.title));
    body.appendChild(element(document, 'p', 'brief-desc', story.summary || story.title));
    var sourceLine = element(document, 'span', 'brief-source');
    sourceLine.appendChild(element(document, 'b', '', story.source || (lang === 'pl' ? 'Źródło' : 'Source')));
    sourceLine.appendChild(element(document, 'span', 'brief-link', lang === 'pl' ? 'Czytaj źródło →' : 'Read source →'));
    body.appendChild(sourceLine);

    card.appendChild(thumb);
    card.appendChild(body);
    return card;
  }

  function candidateStories(data) {
    var output = [];
    var seen = new Set();
    function add(story) {
      if (!story || typeof story !== 'object') return;
      var identity = storyIdentity(story);
      if (!identity || seen.has(identity)) return;
      seen.add(identity);
      output.push(story);
    }

    (Array.isArray(data && data.home) ? data.home : []).forEach(add);
    var sections = data && typeof data.sections === 'object' && data.sections ? data.sections : {};
    var rows = Object.values(sections).filter(Array.isArray);
    var maxRows = rows.reduce(function (max, items) { return Math.max(max, items.length); }, 0);
    for (var index = 0; index < maxRows; index += 1) {
      rows.forEach(function (items) { if (index < items.length) add(items[index]); });
    }
    return output;
  }

  function recoverRemovedCard(container, node, nextSibling, nowMs) {
    if (!node || node.nodeType !== 1 || !node.matches || !node.matches('.brief-card')) return false;
    if (node.dataset.homeCardFloor === 'feed-fallback') return false;
    var published = node.dataset.homePublishedAt;
    if (!isFresh(published, nowMs) || node.dataset.homeStale === 'true') return false;
    ensureFallback(node);
    node.hidden = false;
    node.removeAttribute('aria-hidden');
    if (nextSibling && nextSibling.parentNode === container) container.insertBefore(node, nextSibling);
    else container.appendChild(node);
    return true;
  }

  async function ensureFloor(options) {
    var document = options.document;
    var container = document && document.getElementById('latest-briefs');
    if (!container) return false;
    var nowMs = Number.isFinite(options.nowMs) ? options.nowMs : Date.now();
    var current = visibleCards(container);
    if (current.length >= MIN_CARDS) {
      container.dataset.homeCardFloor = String(MIN_CARDS);
      container.dataset.homeCardCount = String(current.length);
      container.dataset.homeCardFloorStatus = 'met';
      return true;
    }
    if (typeof options.fetchImpl !== 'function') return false;

    try {
      var response = await options.fetchImpl('/data/news/' + options.lang + '.json?v=' + Date.now(), {
        cache: 'no-store',
        headers: { 'Cache-Control': 'no-cache' }
      });
      if (!response || !response.ok) return false;
      var data = await response.json();
      if (!data || data.language !== options.lang || !/^news-live-v[12]$/.test(String(data.schema_version || ''))) return false;

      var identities = new Set(visibleCards(container).map(cardIdentity).filter(Boolean));
      var candidates = candidateStories(data);
      for (var index = 0; index < candidates.length && visibleCards(container).length < MIN_CARDS; index += 1) {
        var story = candidates[index];
        var identity = storyIdentity(story);
        if (!identity || identities.has(identity) || !isFresh(story.published_at, nowMs)) continue;
        var card = makeFallbackCard(document, story, options.lang, nowMs);
        if (!card) continue;
        identities.add(identity);
        container.appendChild(card);
      }
      current = visibleCards(container);
      container.dataset.homeCardFloor = String(MIN_CARDS);
      container.dataset.homeCardCount = String(current.length);
      container.dataset.homeCardFloorStatus = current.length >= MIN_CARDS ? 'met' : 'underfilled';
      return current.length >= MIN_CARDS;
    } catch (error) {
      if (options.console && typeof options.console.warn === 'function') {
        options.console.warn('BriefRooms homepage floor guard could not top up the news cards.', error);
      }
      return false;
    }
  }

  function start(options) {
    var document = options.document;
    var container = document && document.getElementById('latest-briefs');
    if (!container) return false;
    var lang = options.lang === 'en' ? 'en' : 'pl';
    var context = {
      document: document,
      fetchImpl: options.fetchImpl,
      lang: lang,
      console: options.console || { warn: function () {} }
    };
    var scheduled = false;
    function scheduleEnsure() {
      if (scheduled) return;
      scheduled = true;
      setTimeout(function () {
        scheduled = false;
        ensureFloor(context);
      }, 120);
    }

    var observer = new MutationObserver(function (records) {
      var nowMs = Date.now();
      records.forEach(function (record) {
        if (record.type !== 'childList' || !record.removedNodes || !record.removedNodes.length) return;
        Array.from(record.removedNodes).forEach(function (node) {
          if (record.target === container) recoverRemovedCard(container, node, record.nextSibling, nowMs);
        });
      });
      scheduleEnsure();
    });
    observer.observe(container, { childList: true, subtree: true });

    RECHECK_DELAYS_MS.forEach(function (delay) {
      setTimeout(function () { ensureFloor(context); }, delay);
    });
    container.dataset.homeCardFloor = String(MIN_CARDS);
    return true;
  }

  return {
    MIN_CARDS: MIN_CARDS,
    HOME_MAX_AGE_MS: HOME_MAX_AGE_MS,
    candidateStories: candidateStories,
    ensureFallback: ensureFallback,
    ensureFloor: ensureFloor,
    isFresh: isFresh,
    makeFallbackCard: makeFallbackCard,
    recoverRemovedCard: recoverRemovedCard,
    safeHttpUrl: safeHttpUrl,
    start: start,
    visibleCards: visibleCards
  };
});
