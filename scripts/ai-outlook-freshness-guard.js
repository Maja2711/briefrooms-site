(function (root) {
  'use strict';

  var OUTLOOK_RE = /\/data\/ai_outlook\.json(?:\?|$)/i;
  var STATE_KEY = '__BR_AI_OUTLOOK_FRESHNESS__';

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

  function warsawDateKey(date) {
    var formatter = new Intl.DateTimeFormat('en-GB', {
      timeZone: 'Europe/Warsaw',
      year: 'numeric',
      month: '2-digit',
      day: '2-digit'
    });
    var parts = formatter.formatToParts(date || new Date());
    var values = {};
    parts.forEach(function (part) {
      if (part.type !== 'literal') values[part.type] = part.value;
    });
    return [values.year, values.month, values.day].join('-');
  }

  function freshPayload(payload) {
    return Boolean(
      payload &&
      typeof payload === 'object' &&
      String(payload.date || '') === warsawDateKey(new Date()) &&
      Number(payload.schema_version) === 2 &&
      payload.pl && payload.en
    );
  }

  function staleResponse(payload) {
    return new Response(JSON.stringify({
      error: 'stale_ai_outlook',
      expected_date: warsawDateKey(new Date()),
      actual_date: payload && payload.date || null
    }), {
      status: 409,
      headers: { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' }
    });
  }

  function install() {
    if (typeof root.fetch !== 'function' || root.fetch.__brAiOutlookFreshness) return;
    var originalFetch = root.fetch.bind(root);
    var guardedFetch = function (input, init) {
      var url = requestUrl(input);
      if (!url || !OUTLOOK_RE.test(url.pathname + url.search)) {
        return originalFetch(input, init);
      }

      url.searchParams.set('br-fresh', String(Date.now()));
      var nextInit = Object.assign({}, init || {}, { cache: 'no-store' });
      return originalFetch(url.href, nextInit).then(function (response) {
        if (!response || !response.ok) return response;
        return response.clone().json().then(function (payload) {
          if (!freshPayload(payload)) return staleResponse(payload);
          root[STATE_KEY] = {
            status: 'fresh',
            date: payload.date,
            checked_at: new Date().toISOString()
          };
          return new Response(JSON.stringify(payload), {
            status: response.status,
            statusText: response.statusText,
            headers: response.headers
          });
        }).catch(function () {
          return response;
        });
      });
    };
    guardedFetch.__brAiOutlookFreshness = true;
    guardedFetch.__brOriginalFetch = originalFetch;
    root.fetch = guardedFetch;
  }

  install();

  root.BriefRoomsAiOutlookFreshness = {
    freshPayload: freshPayload,
    warsawDateKey: warsawDateKey
  };
})(typeof globalThis !== 'undefined' ? globalThis : this);
