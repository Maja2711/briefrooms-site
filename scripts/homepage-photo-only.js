(function (root) {
  'use strict';

  var HOME_FEED_RE = /\/(?:pl|en)\/home_brief\.json(?:\?|$)/i;

  function approvedPhoto(item) {
    var image = String(item && item.image || '').trim();
    return Boolean(
      item &&
      item.image_policy === 'source-linked-external' &&
      /^https:\/\//i.test(image)
    );
  }

  function filterPayload(payload) {
    if (!payload || typeof payload !== 'object') return payload;
    ['latest', 'radar'].forEach(function (section) {
      if (Array.isArray(payload[section])) {
        payload[section] = payload[section].filter(approvedPhoto);
      }
    });
    return payload;
  }

  function requestUrl(input) {
    try {
      return new URL(
        typeof input === 'string' ? input : input && input.url || '',
        root.location && root.location.href || 'https://briefrooms.com/'
      );
    } catch (error) {
      return null;
    }
  }

  function installFeedFilter() {
    if (typeof root.fetch !== 'function' || root.fetch.__brPhotoOnly) return;
    var originalFetch = root.fetch.bind(root);
    var filteredFetch = function (input, init) {
      return originalFetch(input, init).then(function (response) {
        var url = requestUrl(input);
        if (!url || !HOME_FEED_RE.test(url.pathname + url.search) || !response || !response.ok) {
          return response;
        }
        return response.clone().json().then(function (payload) {
          var body = JSON.stringify(filterPayload(payload));
          return new Response(body, {
            status: response.status,
            statusText: response.statusText,
            headers: response.headers
          });
        }).catch(function () {
          return response;
        });
      });
    };
    filteredFetch.__brPhotoOnly = true;
    filteredFetch.__brOriginalFetch = originalFetch;
    root.fetch = filteredFetch;
  }

  function removeCard(card) {
    if (card && card.parentNode) card.parentNode.removeChild(card);
  }

  function guardCard(card) {
    if (!card || card.dataset.brPhotoGuarded === '1') return;
    card.dataset.brPhotoGuarded = '1';
    var image = card.querySelector('.thumb.has-image img');
    if (!image || !String(image.getAttribute('src') || '').trim()) {
      removeCard(card);
      return;
    }
    image.addEventListener('error', function () {
      removeCard(card);
    }, { once: true });
    if (image.complete && image.naturalWidth === 0) removeCard(card);
  }

  function scan(scope) {
    var container = root.document && root.document.getElementById('latest-briefs');
    if (!container) return;
    container.dataset.homePhotoOnly = 'true';
    var target = scope && scope.querySelectorAll ? scope : container;
    if (target.matches && target.matches('.brief-card')) guardCard(target);
    Array.prototype.forEach.call(target.querySelectorAll('.brief-card'), guardCard);
  }

  function start() {
    scan(root.document);
    var container = root.document.getElementById('latest-briefs');
    if (container && typeof MutationObserver === 'function') {
      new MutationObserver(function (records) {
        records.forEach(function (record) {
          Array.prototype.forEach.call(record.addedNodes || [], function (node) {
            if (node && node.nodeType === 1) scan(node);
          });
        });
      }).observe(container, { childList: true, subtree: true });
    }
  }

  root.BriefRoomsHomepagePhotoOnly = {
    approvedPhoto: approvedPhoto,
    filterPayload: filterPayload,
    guardCard: guardCard,
    scan: scan
  };

  installFeedFilter();
  if (root.document) {
    if (root.document.readyState === 'loading') {
      root.document.addEventListener('DOMContentLoaded', start, { once: true });
    } else {
      start();
    }
  }
})(typeof globalThis !== 'undefined' ? globalThis : this);
