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
  var RECHECK_DELAYS_MS = [250, 800, 1800, 3600];

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

  function safeImageUrl(value) {
    var url = safeHttpUrl(value);
    if (!url) return '';
    try {
      return new URL(url).protocol === 'https:' ? url : '';
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

  function cardImage(card) {
    if (!card || !card.querySelector) return null;
    var image = card.querySelector('.thumb.has-image img');
    if (!image || !safeImageUrl(image.getAttribute('src'))) return null;
    if (image.dataset && image.dataset.homeImageFailed === 'true') return null;
    return image;
  }

  function visibleImageCards(container) {
    return Array.from(container.querySelectorAll(':scope > .brief-card')).filter(function (card) {
      return !card.hidden &&
        card.getAttribute('aria-hidden') !== 'true' &&
        card.dataset.homeStale !== 'true' &&
        Boolean(cardImage(card));
    });
  }

  function element(document, tag, className, text) {
    var node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = String(text);
    return node;
  }

  function makeImageCard(document, story, lang, nowMs) {
    var link = safeHttpUrl(story && story.link);
    var imageUrl = safeImageUrl(story && story.image);
    var published = String(story && story.published_at || '');
    if (!link || !imageUrl || !story || !story.title || !isFresh(published, nowMs)) return null;

    var card = element(document, 'a', 'brief-card');
    card.href = link;
    card.target = '_blank';
    card.rel = 'noopener noreferrer external';
    card.dataset.homePublishedAt = published;
    if (story.homepage_first_seen_at) card.dataset.homeFirstSeenAt = String(story.homepage_first_seen_at);
    card.dataset.homeCardFloor = 'image-feed';

    var thumb = element(document, 'div', 'thumb has-image');
    var image = document.createElement('img');
    image.src = imageUrl;
    image.alt = '';
    image.loading = 'lazy';
    image.decoding = 'async';
    image.referrerPolicy = 'no-referrer';
    image.dataset.brExternalMedia = 'source-linked';
    image.dataset.brSourceUrl = link;
    thumb.appendChild(image);
    thumb.appendChild(element(document, 'span', 'media-source-badge',
      (lang === 'pl' ? 'Źródło: ' : 'Source: ') + String(story.source || '')));

    var body = element(document, 'div', 'brief-body');
    body.appendChild(element(document, 'h3', 'brief-title', story.title));
    body.appendChild(element(document, 'p', 'brief-desc', story.summary || story.title));
    var sourceLine = element(document, 'span', 'brief-source');
    sourceLine.appendChild(element(document, 'b', '', story.source || (lang === 'pl' ? 'Źródło' : 'Source')));
    sourceLine.appendChild(element(document, 'span', 'brief-link',
      lang === 'pl' ? 'Czytaj źródło →' : 'Read source →'));
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
      if (!safeImageUrl(story.image)) return;
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
      rows.forEach(function (items) {
        if (index < items.length) add(items[index]);
      });
    }
    return output;
  }

  function markStatus(container) {
    var count = visibleImageCards(container).length;
    container.dataset.homeCardFloor = String(MIN_CARDS);
    container.dataset.homeCardCount = String(count);
    container.dataset.homeImagePolicy = 'https-image-required-v1';
    container.dataset.homeCardFloorStatus = count >= MIN_CARDS ? 'met' : 'underfilled';
    return count;
  }

  function removeCard(card) {
    if (card && card.parentNode) card.parentNode.removeChild(card);
  }

  function guardCard(card, context) {
    if (!card || !card.matches || !card.matches('.brief-card')) return false;
    if (card.dataset.homeImageFloorGuarded === '1') return Boolean(cardImage(card));

    var image = cardImage(card);
    if (!image) {
      context.failedStoryIds.add(cardIdentity(card));
      removeCard(card);
      context.scheduleEnsure();
      return false;
    }

    card.dataset.homeImageFloorGuarded = '1';
    var failed = false;
    var onFailure = function () {
      if (failed) return;
      failed = true;
      image.dataset.homeImageFailed = 'true';
      context.failedStoryIds.add(cardIdentity(card));
      removeCard(card);
      context.scheduleEnsure();
    };
    image.addEventListener('error', onFailure, { once: true });
    image.addEventListener('load', function () {
      markStatus(context.container);
    }, { once: true });

    if (image.complete && image.naturalWidth === 0) {
      setTimeout(onFailure, 0);
    }
    return true;
  }

  function guardScope(scope, context) {
    if (!scope || !scope.querySelectorAll) return;
    if (scope.matches && scope.matches('.brief-card')) guardCard(scope, context);
    Array.prototype.forEach.call(scope.querySelectorAll('.brief-card'), function (card) {
      guardCard(card, context);
    });
  }

  async function ensureFloor(context) {
    var container = context.container;
    var nowMs = Date.now();
    guardScope(container, context);
    if (markStatus(container) >= MIN_CARDS) return true;
    if (typeof context.fetchImpl !== 'function' || context.running) return false;

    context.running = true;
    try {
      var response = await context.fetchImpl('/data/news/' + context.lang + '.json?v=' + Date.now(), {
        cache: 'no-store',
        headers: { 'Cache-Control': 'no-cache' }
      });
      if (!response || !response.ok) return false;
      var data = await response.json();
      if (!data || data.language !== context.lang || !/^news-live-v[12]$/.test(String(data.schema_version || ''))) {
        return false;
      }

      var identities = new Set(visibleImageCards(container).map(cardIdentity).filter(Boolean));
      var candidates = candidateStories(data);
      for (var index = 0; index < candidates.length && visibleImageCards(container).length < MIN_CARDS; index += 1) {
        var story = candidates[index];
        var identity = storyIdentity(story);
        if (!identity || identities.has(identity) || context.failedStoryIds.has(identity)) continue;
        if (!isFresh(story.published_at, nowMs)) continue;
        if (story.homepage_first_seen_at && !isFresh(story.homepage_first_seen_at, nowMs)) continue;

        var card = makeImageCard(context.document, story, context.lang, nowMs);
        if (!card) continue;
        identities.add(identity);
        container.appendChild(card);
        guardCard(card, context);
      }
      return markStatus(container) >= MIN_CARDS;
    } catch (error) {
      if (context.console && typeof context.console.warn === 'function') {
        context.console.warn('BriefRooms image-only homepage could not replenish ten cards.', error);
      }
      return false;
    } finally {
      context.running = false;
      markStatus(container);
    }
  }

  function start(options) {
    var document = options.document;
    var container = document && document.getElementById('latest-briefs');
    if (!container) return false;

    var context = {
      document: document,
      container: container,
      fetchImpl: options.fetchImpl,
      lang: options.lang === 'en' ? 'en' : 'pl',
      console: options.console || { warn: function () {} },
      failedStoryIds: new Set(),
      running: false,
      scheduled: false,
      scheduleEnsure: null
    };

    context.scheduleEnsure = function () {
      if (context.scheduled) return;
      context.scheduled = true;
      setTimeout(function () {
        context.scheduled = false;
        ensureFloor(context);
      }, 80);
    };

    guardScope(container, context);
    markStatus(container);

    if (typeof MutationObserver === 'function') {
      new MutationObserver(function (records) {
        records.forEach(function (record) {
          Array.prototype.forEach.call(record.removedNodes || [], function (node) {
            if (!node || node.nodeType !== 1 || !node.matches || !node.matches('.brief-card')) return;
            var image = node.querySelector && node.querySelector('.thumb.has-image img');
            if (!image || image.dataset.homeImageFailed === 'true' || (image.complete && image.naturalWidth === 0)) {
              context.failedStoryIds.add(cardIdentity(node));
            }
          });
          Array.prototype.forEach.call(record.addedNodes || [], function (node) {
            if (node && node.nodeType === 1) guardScope(node, context);
          });
        });
        context.scheduleEnsure();
      }).observe(container, { childList: true, subtree: true });
    }

    RECHECK_DELAYS_MS.forEach(function (delay) {
      setTimeout(function () { ensureFloor(context); }, delay);
    });
    context.scheduleEnsure();
    return true;
  }

  return {
    MIN_CARDS: MIN_CARDS,
    HOME_MAX_AGE_MS: HOME_MAX_AGE_MS,
    candidateStories: candidateStories,
    ensureFloor: ensureFloor,
    isFresh: isFresh,
    makeImageCard: makeImageCard,
    safeHttpUrl: safeHttpUrl,
    safeImageUrl: safeImageUrl,
    start: start,
    visibleImageCards: visibleImageCards
  };
});
